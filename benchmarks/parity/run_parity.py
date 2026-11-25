#!/usr/bin/env python3
"""Outcome parity across Oximut execution modes on pinned real open-source Vitest projects.

For every project in corpus.json this fetches the pinned commit, runs Oximut once per
execution mode and compares each mode's per-mutant outcomes with the reference mode
(`--execution fresh --no-zygote`). The comparison key is the exact edited-file identity
the other harnesses use (benchmarks/m6.py), so `mutation_identity_outcome_sha256` here
means the same thing as in the colord benchmark reports.

Any difference is a failure: a mismatch prints one line per mutant and the command exits 1.
This harness never retries a run and never hides a difference.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'benchmarks'))
import m6  # noqa: E402  (exact edited-file identity and digest shared with the other harnesses)

CORPUS = HERE / 'corpus.json'
WORK_DIR = ROOT / '.parity-work'
COUNTER_KEYS = ('zygotes', 'forks', 'exits', 'zygoteFailures', 'fallbacks')
REFERENCE = 'reference'

# (name, extra Oximut arguments, diagnostics that must be reported, diagnostics that must be absent)
MODES = (
    (REFERENCE, ('--execution', 'fresh', '--no-zygote'), (), ('zygote=on',)),
    ('persistent', ('--execution', 'persistent', '--no-zygote'), (), ('zygote=on',)),
    ('zygote', ('--execution', 'persistent', '--zygote'), ('zygote=on',), ()),
    ('zygote-selection-precollect-rolling',
     ('--execution', 'persistent', '--zygote', '--test-selection', '--zygote-precollect', '--rolling-lanes'),
     ('zygote=on', 'zygote-precollect=on', 'rolling-lanes=on'), ()),
)
MODE_NAMES = tuple(name for name, *_ in MODES)


class ParityError(Exception):
    """A run could not be compared (as opposed to compared and found different)."""


def load_corpus(path=CORPUS):
    corpus = json.loads(Path(path).read_text())
    names = [project['name'] for project in corpus['projects']]
    if len(set(names)) != len(names):
        raise ParityError(f'duplicate project names in {path}')
    for project in corpus['projects']:
        if len(project['commit']) != 40 or any(c not in '0123456789abcdef' for c in project['commit']):
            raise ParityError(f"{project['name']}: commit must be a full lowercase sha1")
    return corpus


# --- pure comparison logic -------------------------------------------------------------

def mutant_records(report, project_dir):
    """Map exact edited-file identity -> record for every mutant in an Oximut report."""
    project_dir = Path(project_dir)
    sources = {}
    records = {}
    for mutant in report['mutants']:
        relative = Path(mutant['file'])
        if relative.is_absolute():
            relative = relative.relative_to(project_dir)
        filename = relative.as_posix()
        if filename not in sources:
            sources[filename] = (project_dir / relative).read_text()
        identity = m6.mutation_identity(filename, sources[filename], mutant['location'], mutant['replacement'])
        if identity in records:
            raise ParityError(f'duplicate exact edited-file identity {identity}; the mutation scope is ambiguous')
        records[identity] = {
            'id': mutant['id'], 'file': filename, 'line': mutant['location']['start']['line'],
            'operator': mutant.get('operator', mutant.get('mutatorName')),
            'replacement': mutant['replacement'], 'outcome': mutant['outcome'],
        }
    return records


def outcome_digest(records):
    """`mutation_identity_outcome_sha256`: digest of {identity: outcome}, as in benchmarks/m6.py."""
    return m6.digest({identity: record['outcome'] for identity, record in records.items()})


def compare_records(reference, other):
    """Per-mutant differences of `other` against `reference`; empty when identical."""
    differences = []
    for identity in sorted(reference.keys() | other.keys()):
        expected, actual = reference.get(identity), other.get(identity)
        if expected is None or actual is None:
            present = expected or actual
            differences.append({
                'identity': identity, 'id': present['id'], 'file': present['file'], 'line': present['line'],
                'operator': present['operator'],
                'reference': expected['outcome'] if expected else '<absent>',
                'mode': actual['outcome'] if actual else '<absent>',
            })
        elif expected['outcome'] != actual['outcome']:
            differences.append({
                'identity': identity, 'id': expected['id'], 'file': expected['file'], 'line': expected['line'],
                'operator': expected['operator'],
                'reference': expected['outcome'], 'mode': actual['outcome'],
            })
    differences.sort(key=lambda d: (d['file'], d['line'], d['id']))
    return differences


def format_differences(project, mode, differences):
    lines = [f'MISMATCH {project} / {mode}: {len(differences)} mutant(s) differ from the reference']
    for d in differences:
        lines.append(f"  {d['id']} {d['file']}:{d['line']} {d['operator']}: "
                     f"reference={d['reference']} {mode}={d['mode']}")
    return '\n'.join(lines)


def check_engagement(mode, report, counters):
    """Problems showing the mode did not actually run the way its name claims."""
    problems = []
    diagnostics = '\n'.join(report.get('diagnostics', []))
    spec = {name: (needed, forbidden) for name, _, needed, forbidden in MODES}[mode]
    for needle in spec[0]:
        if needle not in diagnostics:
            problems.append(f'{mode}: report lacks diagnostic {needle!r}')
    for needle in spec[1]:
        if needle in diagnostics:
            problems.append(f'{mode}: report unexpectedly has diagnostic {needle!r}')
    if 'zygote=on' in spec[0]:
        if counters['forks'] <= 0:
            problems.append(f'{mode}: the zygote forked nothing ({counters})')
        if counters['zygoteFailures'] != 0:
            problems.append(f'{mode}: zygote failures ({counters})')
    elif any(counters.values()):
        problems.append(f'{mode}: wrote zygote counters although the zygote is off ({counters})')
    return problems


def check_run(mode, report, exit_code):
    """A run is comparable only when it finished: survivors (exit 1) are fine, incomplete (2) is not."""
    problems = []
    summary = report.get('summary', {})
    if exit_code not in (0, 1):
        problems.append(f'{mode}: exit code {exit_code}')
    if summary.get('complete') is not True:
        problems.append(f'{mode}: run is not complete: {summary}')
    if not report.get('mutants'):
        problems.append(f'{mode}: report has no mutants')
    return problems


# --- running ---------------------------------------------------------------------------

def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()


def fetch_project(project, work_dir=WORK_DIR):
    """Check out the pinned commit under work_dir (inside the repo so node resolves its Vitest)."""
    work_dir = Path(work_dir).resolve()
    try:
        work_dir.relative_to(ROOT)
    except ValueError:
        raise ParityError(f'--work-dir must be inside {ROOT} so projects resolve the repository vitest') from None
    directory = work_dir / project['name']
    marker = directory / '.parity-commit'
    if marker.is_file() and marker.read_text().strip() == project['commit']:
        return directory
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    git('init', '-q', cwd=directory)
    git('fetch', '-q', '--depth', '1', project['repository'], project['commit'], cwd=directory)
    git('checkout', '-q', '--detach', 'FETCH_HEAD', cwd=directory)
    head = git('rev-parse', 'HEAD', cwd=directory)
    if head != project['commit']:
        raise ParityError(f"{project['name']}: fetched {head}, expected {project['commit']}")
    shutil.rmtree(directory / '.git')
    shutil.copyfile(HERE / project['vitestConfig'], directory / project['configFile'])
    marker.write_text(project['commit'] + '\n')
    return directory


def oximut_command(binary, project, project_dir, mode_args, report, workers):
    command = [str(binary), '--root', str(project_dir), '--config', project['configFile'],
               '--workers', str(workers), '--no-cache', '--report', str(report), *mode_args]
    for source in project['mutate']:
        command.extend(['--source', source])
    if project.get('mutators'):
        command.extend(['--mutators', ','.join(project['mutators'])])
    return command


def read_counters(directory):
    totals = {key: 0 for key in COUNTER_KEYS}
    for file in sorted(directory.iterdir()):
        for key, value in json.loads(file.read_text()).items():
            if key in totals:
                totals[key] += value
    return totals


def run_mode(binary, project, project_dir, mode, mode_args, workers, timeout, directory):
    report_path = directory / f"{project['name']}-{mode}.json"
    counters_dir = directory / f"{project['name']}-{mode}-counters"
    counters_dir.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith('OXIMUT_ZYGOTE')}
    env['OXIMUT_ZYGOTE_COUNTERS_DIR'] = str(counters_dir)
    command = oximut_command(binary, project, project_dir, mode_args, report_path, workers)
    started = time.monotonic()
    try:
        result = subprocess.run(command, env=env, cwd=ROOT, text=True, timeout=timeout,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        raise ParityError(f"{project['name']} / {mode}: timed out after {timeout:g} s") from None
    wall = time.monotonic() - started
    if not report_path.is_file():
        raise ParityError(f"{project['name']} / {mode}: no report written (exit {result.returncode})\n"
                          + result.stdout[-4000:])
    report = json.loads(report_path.read_text())
    return {'exit_code': result.returncode, 'wall_seconds': round(wall, 1), 'report': report,
            'counters': read_counters(counters_dir), 'output_tail': result.stdout[-3000:]}


def evaluate_project(binary, project, project_dir, modes, workers, timeout, directory):
    """Run `modes` for one project; returns the project's result record."""
    records, runs, problems, differences = {}, {}, [], {}
    for mode, mode_args, _, _ in MODES:
        if mode not in modes:
            continue
        run = run_mode(binary, project, project_dir, mode, mode_args, workers, timeout, directory)
        runs[mode] = run
        mode_problems = check_run(mode, run['report'], run['exit_code']) + \
            check_engagement(mode, run['report'], run['counters'])
        if mode_problems:
            problems.extend(mode_problems)
            print(f"::group::{project['name']} / {mode} output tail", flush=True)
            print(run['output_tail'], flush=True)
            print('::endgroup::', flush=True)
        records[mode] = mutant_records(run['report'], project_dir)
    if REFERENCE in records:
        for mode, other in records.items():
            if mode != REFERENCE:
                found = compare_records(records[REFERENCE], other)
                if found:
                    differences[mode] = found
    modes_out = {}
    for mode, run in runs.items():
        resources = run['report'].get('resources', {})
        modes_out[mode] = {
            'mutation_identity_outcome_sha256': outcome_digest(records[mode]),
            'mutants': len(records[mode]),
            'summary': run['report'].get('summary'),
            'exit_code': run['exit_code'], 'wall_seconds': run['wall_seconds'],
            'zygote_counters': run['counters'],
            'effective_workers': resources.get('effectiveWorkers', resources.get('workers')),
            'diagnostics': run['report'].get('diagnostics', []),
        }
    return {'name': project['name'], 'repository': project['repository'], 'commit': project['commit'],
            'modes': modes_out, 'problems': problems,
            'mismatches': {mode: found for mode, found in differences.items()},
            'outcomes': {mode: {identity: record['outcome'] for identity, record in recs.items()}
                         for mode, recs in records.items()}}


def markdown_table(results):
    lines = ['| project | commit | mutants | mode | mutation_identity_outcome_sha256 | match | wall s |',
             '| --- | --- | ---: | --- | --- | --- | ---: |']
    for result in results:
        reference = result['modes'].get(REFERENCE, {}).get('mutation_identity_outcome_sha256')
        for mode, data in result['modes'].items():
            match = 'reference' if mode == REFERENCE else \
                ('yes' if data['mutation_identity_outcome_sha256'] == reference else '**NO**')
            lines.append(f"| {result['name']} | {result['commit'][:10]} | {data['mutants']} | {mode} | "
                         f"`{data['mutation_identity_outcome_sha256']}` | {match} | {data['wall_seconds']} |")
    return '\n'.join(lines)


def list_mutants(binary, project, project_dir):
    command = [str(binary), '--root', str(project_dir), '--config', project['configFile'], '--list']
    for source in project['mutate']:
        command.extend(['--source', source])
    if project.get('mutators'):
        command.extend(['--mutators', ','.join(project['mutators'])])
    result = subprocess.run(command, cwd=ROOT, text=True, check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)['mutants']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/release/oximut')
    parser.add_argument('--corpus', type=Path, default=CORPUS)
    parser.add_argument('--work-dir', type=Path, default=WORK_DIR)
    parser.add_argument('--output', type=Path, help='directory for reports and summary.json (default: temporary)')
    parser.add_argument('--projects', nargs='*', help='project names (default: all)')
    parser.add_argument('--modes', nargs='*', choices=MODE_NAMES, default=list(MODE_NAMES))
    parser.add_argument('--workers', type=int, help='default: corpus workers')
    parser.add_argument('--timeout', type=float, default=1500, help='seconds per Oximut run')
    parser.add_argument('--list-projects', action='store_true', help='print the project names as JSON and exit')
    parser.add_argument('--count-mutants', action='store_true',
                        help='fetch the projects and print their --list mutant counts, then exit')
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    projects = corpus['projects']
    if args.projects:
        unknown = set(args.projects) - {project['name'] for project in projects}
        if unknown:
            parser.error(f'unknown projects: {sorted(unknown)}')
        projects = [project for project in projects if project['name'] in args.projects]
    if args.list_projects:
        print(json.dumps([project['name'] for project in projects]))
        return 0
    if REFERENCE not in args.modes:
        parser.error('the reference mode must be among --modes')
    workers = args.workers or corpus['workers']

    if args.count_mutants:
        for project in projects:
            project_dir = fetch_project(project, args.work_dir)
            mutants = list_mutants(args.binary, project, project_dir)
            print(f"{project['name']}: {len(mutants)} mutants")
        return 0

    output = args.output or Path(tempfile.mkdtemp(prefix='oximut-parity-'))
    output.mkdir(parents=True, exist_ok=True)
    results, failed = [], False
    for project in projects:
        print(f"== {project['name']} @ {project['commit']}", flush=True)
        project_dir = fetch_project(project, args.work_dir)
        try:
            result = evaluate_project(args.binary, project, project_dir, args.modes, workers,
                                      args.timeout, output)
        except ParityError as error:
            print(f'FAIL: {error}', file=sys.stderr)
            failed = True
            continue
        results.append(result)
        for problem in result['problems']:
            print(f'FAIL: {project["name"]}: {problem}', file=sys.stderr)
        for mode, found in result['mismatches'].items():
            print(format_differences(project['name'], mode, found), file=sys.stderr)
        failed |= bool(result['problems'] or result['mismatches'])
    (output / 'summary.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
    table = markdown_table(results)
    print(table)
    summary_file = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_file:
        with open(summary_file, 'a') as handle:
            handle.write('### Outcome parity\n\n' + table + '\n')
            for result in results:
                for mode, found in result['mismatches'].items():
                    handle.write('\n```\n' + format_differences(result['name'], mode, found) + '\n```\n')
    if failed:
        return 1
    print(f'outcome parity ok: {len(results)} project(s), modes {", ".join(args.modes)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
