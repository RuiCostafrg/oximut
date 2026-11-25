#!/usr/bin/env python3
"""Oximut (default flags) against StrykerJS on several pinned real Vitest projects.

One invocation measures one cell: one project at one matched worker count. Per round it
runs plain Vitest (suite time, for diagnosis), Oximut with its defaults and Stryker with
its Vitest runner and `coverageAnalysis: perTest`, sequentially and in alternating order,
in clean private copies with no cache. It writes `cell.json`; `multi_project_report.py`
turns cells into the comparison tables.

Projects are the outcome-parity corpus (benchmarks/parity/corpus.json: tiny scopes that
measure start-up cost) plus larger projects pinned in benchmarks/multi-project/projects.json
(hundreds of mutants: per-mutant cost). Both tools mutate exactly the same files.

Mutant sets are not forced equal, unlike the colord harness: the tools generate different
mutants, so each run records both identity sets (the exact edited-file identity of
benchmarks/m6.py) and how they overlap, and the outcome counts of each tool.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / 'parity'))
import m6  # noqa: E402
import real_project  # noqa: E402
import run as measurement  # noqa: E402
import run_parity  # noqa: E402

SCHEMA_VERSION = 1
PROJECTS_FILE = HERE / 'multi-project/projects.json'
PARITY_DIR = HERE / 'parity'
WORK_DIR = ROOT / '.multi-project-work'
CONFIG_NAME = 'bench.vitest.config.ts'
TOOLS = ('vitest', 'oximut', 'stryker')
SCOPES = ('matched', 'full')
KINDS = ('startup', 'large')
# Oximut generates exactly these families; Stryker's operators that cover the same kinds of edit.
STRYKER_FAMILIES = {**real_project.STRYKER_FAMILIES, 'comparison-boundary': 'EqualityOperator'}
STRYKER_OPERATORS = real_project.STRYKER_ALWAYS_EXCLUDED + list(real_project.STRYKER_FAMILIES.values())
DETECTED = frozenset({'killed', 'timeout'})
UNDETECTED = frozenset({'survived', 'no-coverage'})
STRYKER_STATUS = {'Killed': 'killed', 'Survived': 'survived', 'NoCoverage': 'no-coverage',
                  'Timeout': 'timeout', 'RuntimeError': 'invalid', 'CompileError': 'invalid',
                  'Ignored': 'ignored'}
# An Oximut outcome that means the engine, not the mutant, failed: the run says nothing about speed.
ENGINE_FAILURES = frozenset({'infrastructure-error', 'resource-exhausted', 'skipped'})
# tsconfig files are left out of every private copy: Stryker 10.1.1 rewrites any tsconfig it finds with
# `ts.parseConfigFileTextToJson`, which the repository's TypeScript 7 no longer has, so Stryker would
# crash on projects that ship one. Vitest and Oximut do not need them to run the sources, and both tools
# see the same copy.
COPY_IGNORE = shutil.ignore_patterns('node_modules', '.oximut', '.vitest', '.stryker-tmp*', '.parity-commit',
                                     'tsconfig*.json')


class BenchmarkError(Exception):
    """The campaign cannot produce a trustworthy measurement."""


# --- projects and scope ------------------------------------------------------------------

def load_projects(projects_file=PROJECTS_FILE, parity_corpus=run_parity.CORPUS):
    """Every benchmark project: the parity corpus (`startup`) then the pinned large ones."""
    projects = []
    for entry in run_parity.load_corpus(parity_corpus)['projects']:
        projects.append({**entry, 'kind': 'startup', 'root': '.', 'copy': None,
                         'configSource': str(PARITY_DIR / entry['vitestConfig'])})
    for entry in json.loads(Path(projects_file).read_text())['projects']:
        entry = {**entry, 'configSource': str(Path(projects_file).parent / entry['vitestConfig'])}
        entry.setdefault('copy', None)
        entry.setdefault('root', '.')
        projects.append(entry)
    names = [project['name'] for project in projects]
    if len(set(names)) != len(names):
        raise BenchmarkError(f'duplicate project names: {names}')
    for project in projects:
        if project['kind'] not in KINDS:
            raise BenchmarkError(f"{project['name']}: kind must be one of {KINDS}")
        commit = project['commit']
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            raise BenchmarkError(f"{project['name']}: commit must be a full lowercase sha1")
    return projects


def select_projects(projects, names):
    """The named projects in corpus order; an unknown name is an error, not a silent skip."""
    if not names:
        return list(projects)
    known = {project['name'] for project in projects}
    unknown = sorted(set(names) - known)
    if unknown:
        raise BenchmarkError(f'unknown projects: {unknown}; choose from {sorted(known)}')
    return [project for project in projects if project['name'] in set(names)]


def resolve_mutate(project, directory):
    """Concrete, sorted source files of the mutation scope; patterns that match nothing are errors."""
    directory = Path(directory)
    found = set()
    for pattern in project['mutate']:
        if any(char in pattern for char in '*?['):
            matches = {path.relative_to(directory).as_posix()
                       for path in directory.glob(pattern) if path.is_file()}
        else:
            matches = {pattern} if (directory / pattern).is_file() else set()
        if not matches:
            raise BenchmarkError(f"{project['name']}: mutate pattern {pattern!r} matches no file")
        found |= matches
    excluded = project.get('mutateExclude', [])
    files = sorted(name for name in found if not any(fnmatch.fnmatchcase(name, p) for p in excluded))
    if not files:
        raise BenchmarkError(f"{project['name']}: the mutation scope is empty after exclusions")
    return files


def stryker_excluded(scope):
    """Stryker operators to switch off: all but Oximut's families when matched, none when full."""
    if scope == 'full':
        return []
    kept = set(STRYKER_FAMILIES.values())
    return [operator for operator in STRYKER_OPERATORS if operator not in kept]


def stryker_config(files, workers, scope, related=False):
    config = {
        'mutate': files,
        'ignorePatterns': ['node_modules', '.stryker-tmp*', '.oximut'],
        'testRunner': 'vitest', 'vitest': {'configFile': CONFIG_NAME, 'related': related},
        'coverageAnalysis': 'perTest', 'concurrency': workers, 'checkers': [],
        'incremental': False, 'reporters': ['json'],
        'tempDirName': '.stryker-tmp-multi-project', 'cleanTempDir': True,
    }
    excluded = stryker_excluded(scope)
    if excluded:
        config['mutator'] = {'excludedMutations': excluded}
    return config


def write_stryker_config(project_dir, config):
    (project_dir / 'stryker.config.mjs').write_text(
        'export default { ...' + json.dumps(config) +
        ', jsonReporter: { fileName: process.env.OXIMUT_BENCH_REPORT } };\n')


# --- checkout and clean private copies -----------------------------------------------------

def fetch_checkout(project, work_dir=WORK_DIR):
    """The pinned commit, without git metadata, cached under work_dir."""
    directory = Path(work_dir) / project['name']
    marker = directory / '.parity-commit'
    if marker.is_file() and marker.read_text().strip() == project['commit']:
        return directory
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    run_parity.git('init', '-q', cwd=directory)
    run_parity.git('fetch', '-q', '--depth', '1', project['repository'], project['commit'], cwd=directory)
    run_parity.git('checkout', '-q', '--detach', 'FETCH_HEAD', cwd=directory)
    head = run_parity.git('rev-parse', 'HEAD', cwd=directory)
    if head != project['commit']:
        raise BenchmarkError(f"{project['name']}: fetched {head}, expected {project['commit']}")
    shutil.rmtree(directory / '.git')
    marker.write_text(project['commit'] + '\n')
    return directory


def project_source(project, checkout):
    return Path(checkout) / project.get('root', '.')


def prepare_run(project, source, destination):
    """A clean private copy of the project, the benchmark Vitest config and the shared node_modules."""
    source, destination = Path(source), Path(destination)
    copy = project.get('copy')
    if copy is None:
        shutil.copytree(source, destination, ignore=COPY_IGNORE)
    else:
        destination.mkdir(parents=True)
        for name in copy:
            origin = source / name
            if origin.is_dir():
                shutil.copytree(origin, destination / name, ignore=COPY_IGNORE)
            elif origin.is_file():
                shutil.copyfile(origin, destination / name)
            else:
                raise BenchmarkError(f"{project['name']}: copy path {name!r} is absent from the checkout")
    shutil.copyfile(project['configSource'], destination / CONFIG_NAME)
    (destination / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)


# --- commands -------------------------------------------------------------------------------

def tool_command(tool, binary, project_dir, files, workers, report):
    if tool == 'vitest':
        return ['node', str(ROOT / 'node_modules/vitest/vitest.mjs'), 'run', '--config', CONFIG_NAME,
                '--reporter=json', f'--outputFile={report}']
    if tool == 'stryker':
        return ['node', str(ROOT / 'node_modules/@stryker-mutator/core/bin/stryker.js'),
                'run', 'stryker.config.mjs']
    # Oximut's defaults: no execution, selection or zygote flags. Only the cache is bypassed,
    # so every run is clean, and the scope and report path are stated.
    command = [str(binary), '--root', str(project_dir), '--config', CONFIG_NAME,
               '--workers', str(workers), '--no-cache', '--report', str(report)]
    for name in files:
        command.extend(['--source', name])
    return command


# --- reports -> comparable records ------------------------------------------------------------

def outcome_class(outcome):
    if outcome in DETECTED:
        return 'detected'
    if outcome in UNDETECTED:
        return 'undetected'
    return 'other'


def oximut_outcomes(report, project_dir):
    """{exact edited-file identity: outcome} of an Oximut report."""
    try:
        records = run_parity.mutant_records(report, project_dir)
    except run_parity.ParityError as error:
        raise BenchmarkError(str(error)) from error
    return {identity: record['outcome'] for identity, record in records.items()}


def stryker_outcomes(report, project_dir):
    """({identity: outcome}, ignored count, duplicate identity count) of a Stryker JSON report."""
    project_dir = Path(project_dir)
    outcomes, ignored, duplicates = {}, 0, 0
    for filename, data in report['files'].items():
        relative = Path(filename)
        if relative.is_absolute():
            relative = relative.relative_to(project_dir)
        source = data.get('source')
        if source is None:
            source = (project_dir / relative).read_text()
        for mutant in data.get('mutants', []):
            status = STRYKER_STATUS.get(mutant['status'])
            if status is None:
                raise BenchmarkError(f"unknown Stryker mutant status {mutant['status']!r}")
            if status == 'ignored':
                ignored += 1
                continue
            identity = m6.mutation_identity(relative.as_posix(), source, mutant['location'], mutant['replacement'])
            if identity in outcomes:
                # Two Stryker operators produced the same edit; both are real mutants it ran.
                duplicates += 1
                identity = f'{identity}#{duplicates}'
            outcomes[identity] = status
    return outcomes, ignored, duplicates


def count_outcomes(outcomes):
    counts = {}
    for outcome in outcomes.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return dict(sorted(counts.items()))


def detection_rate(counts):
    """(killed + timeout) / (killed + timeout + survived + no-coverage); None when nothing counts."""
    detected = sum(counts.get(name, 0) for name in DETECTED)
    total = detected + sum(counts.get(name, 0) for name in UNDETECTED)
    return detected / total if total else None


def compare_scope(oximut, stryker, limit=25):
    """How the two tools' mutant sets overlap, and whether they agree on the shared mutants."""
    shared = sorted(oximut.keys() & stryker.keys())
    disagree = [{'identity': identity, 'oximut': oximut[identity], 'stryker': stryker[identity]}
                for identity in shared
                if outcome_class(oximut[identity]) != outcome_class(stryker[identity])]
    return {'oximut_mutants': len(oximut), 'stryker_mutants': len(stryker), 'shared': len(shared),
            'oximut_only': len(oximut.keys() - stryker.keys()),
            'stryker_only': len(stryker.keys() - oximut.keys()),
            'shared_agree': len(shared) - len(disagree), 'shared_disagree': len(disagree),
            'disagreements': disagree[:limit]}


STRYKER_INITIAL = re.compile(
    r'Initial test run succeeded\. Ran (\d+) tests? in ([\d.]+) seconds?\s*\(net (\d+) ms, overhead (\d+) ms\)')
STRYKER_PER_MUTANT = re.compile(r'Ran ([\d.]+) tests? per mutant on average')


def parse_stryker_log(text):
    """Start-up evidence from Stryker's own log; absent fields are None, never guessed."""
    initial = STRYKER_INITIAL.search(text)
    per_mutant = STRYKER_PER_MUTANT.search(text)
    return {'initial_run_seconds': float(initial.group(2)) if initial else None,
            'initial_run_net_ms': int(initial.group(3)) if initial else None,
            'initial_run_overhead_ms': int(initial.group(4)) if initial else None,
            'tests_per_mutant': float(per_mutant.group(1)) if per_mutant else None}


def cpu_model():
    """The runner's CPU model, as the kernel reports it."""
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key.strip() in ('model name', 'Model name', 'Hardware') and value.strip():
                return value.strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or 'unknown'


def usable_cpus():
    return len(os.sched_getaffinity(0))


# --- one measured run ----------------------------------------------------------------------

RUN_METRICS = ('wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes', 'peak_process_count')


def harness_errors(row, allowed_exit):
    errors = []
    if row.get('exit_code') not in allowed_exit:
        errors.append(f"exit code {row.get('exit_code')}")
    for key, text in (('timed_out', 'timed out'), ('descendant_cleanup_required', 'left descendants running'),
                      ('cleanup_error', 'could not be cleaned up'), ('spawn_error', 'failed to spawn'),
                      ('memory_errors', 'memory accounting failed')):
        if row.get(key):
            errors.append(text)
    return errors


def analyze(tool, row, report_path, project_dir, workers):
    """Validate one finished run and return its record fields; raises BenchmarkError when unusable."""
    errors = harness_errors(row, (0, 1, 2) if tool == 'oximut' else (0,))
    if errors:
        raise BenchmarkError(f'{tool}: {"; ".join(errors)}')
    try:
        report = measurement.read_json(report_path)
    except (OSError, ValueError) as error:
        raise BenchmarkError(f'{tool}: no readable report: {error}') from error
    if tool == 'vitest':
        if report.get('numFailedTests') or not report.get('numTotalTests'):
            raise BenchmarkError(f"vitest: {report.get('numFailedTests')} failed of {report.get('numTotalTests')} tests")
        return {'tests': report['numTotalTests']}
    if tool == 'oximut':
        summary = report.get('summary', {})
        baseline = report.get('baseline', {})
        if baseline.get('outcome') != 'passed':
            raise BenchmarkError(f"oximut: baseline {baseline.get('outcome')!r}")
        try:
            outcomes = oximut_outcomes(report, project_dir)
        except (KeyError, ValueError, OSError) as error:
            raise BenchmarkError(f'oximut: unreadable report: {error}') from error
        failures = sorted(set(outcomes.values()) & ENGINE_FAILURES)
        if failures:
            raise BenchmarkError(f'oximut: engine failure outcomes {failures}')
        resources = report.get('resources') if isinstance(report.get('resources'), dict) else {}
        unmatched = real_project.parallelism_errors({'resources': resources}, workers)
        if unmatched:
            raise BenchmarkError('oximut: ' + '; '.join(unmatched))
        return {'outcomes': outcomes, 'complete': bool(summary.get('complete')),
                'tests': baseline.get('tests', {}).get('passed'),
                'diagnostics': [line for line in report.get('diagnostics', []) if '=' in line][:12],
                'phase_wall_ms': resources.get('phaseWallMs')}
    try:
        outcomes, ignored, duplicates = stryker_outcomes(report, project_dir)
    except (KeyError, ValueError, OSError) as error:
        raise BenchmarkError(f'stryker: unreadable report: {error}') from error
    if not outcomes:
        raise BenchmarkError('stryker: report has no mutants')
    log = Path(row['log_file']).read_text(errors='replace') if row.get('log_file') else ''
    return {'outcomes': outcomes, 'ignored': ignored, 'duplicate_identities': duplicates,
            'tests': sum(len(suite['tests']) for suite in report.get('testFiles', {}).values()),
            'log': parse_stryker_log(log)}


def record(tool, row, extra, round_index, warmup):
    """The stored form of a run: raw measurements plus compact, comparable evidence."""
    stored = {key: row.get(key) for key in (*RUN_METRICS, 'exit_code')}
    stored.update(tool=tool, round=round_index, warmup=warmup, valid=True)
    outcomes = extra.pop('outcomes', None)
    if outcomes is not None:
        stored.update(mutants=len(outcomes), counts=count_outcomes(outcomes),
                      outcome_sha256=m6.digest(outcomes))
    stored.update(extra)
    return stored, outcomes


def failed_record(tool, row, error, round_index, warmup):
    stored = {key: row.get(key) for key in (*RUN_METRICS, 'exit_code')}
    stored.update(tool=tool, round=round_index, warmup=warmup, valid=False, error=str(error),
                  log_tail=(Path(row['log_file']).read_text(errors='replace')[-4000:]
                            if row.get('log_file') and Path(row['log_file']).is_file() else ''))
    return stored


def run_cell(args, project, checkout):
    """Run every round of one cell. Returns the cell record; never retries or drops a run."""
    source = project_source(project, checkout)
    files = resolve_mutate(project, source)
    cell = {
        'schemaVersion': SCHEMA_VERSION, 'status': 'ok', 'error': None,
        'project': {key: project[key] for key in ('name', 'kind', 'repository', 'commit', 'description')},
        'scope': args.scope, 'workers': args.workers, 'warmups': args.warmups, 'rounds': args.rounds,
        'stryker_related': args.stryker_related, 'mutate': files,
        'stryker_excluded_mutations': stryker_excluded(args.scope),
        'machine': {'cpu_model': cpu_model(), 'usable_cpus': usable_cpus(), 'platform': platform.platform()},
        'versions': measurement.metadata()['versions'],
        'oximut': {'binary': str(args.binary), 'binary_sha256': measurement.file_hash(args.binary),
                   'arguments': 'defaults (--no-cache only)', 'git_revision': measurement.capture(['git', 'rev-parse', 'HEAD'])},
        'runs': [], 'first_outcomes': {}, 'scope_comparison': None,
    }
    maps = {}
    config = stryker_config(files, args.workers, args.scope, args.stryker_related)
    for index in range(args.warmups + args.rounds):
        tools = list(args.tools) if index % 2 == 0 else list(reversed(args.tools))
        warmup = index < args.warmups
        for tool in tools:
            directory = args.output / f'{index:02d}-{tool}'
            directory.mkdir(parents=True)
            project_dir, report = directory / 'project', directory / 'report.json'
            prepare_run(project, source, project_dir)
            if tool == 'stryker':
                write_stryker_config(project_dir, config)
            env = {**os.environ, 'CI': 'true', 'NO_COLOR': '1', 'OXIMUT_BENCH_REPORT': str(report)}
            command = tool_command(tool, args.binary, project_dir, files, args.workers, report)
            print(f'round {index}{" (warmup)" if warmup else ""}: {tool}', flush=True)
            row = measurement.run_command(command, project_dir, directory / 'output.log',
                                          args.timeout, .02, 16_777_216, env)
            try:
                extra = analyze(tool, row, report, project_dir, args.workers)
            except BenchmarkError as error:
                cell['runs'].append(failed_record(tool, row, error, index, warmup))
                cell['status'], cell['error'] = 'failed', f'round {index} {tool}: {error}'
                shutil.rmtree(project_dir, ignore_errors=True)
                print(f'  FAILED: {error}', flush=True)
                return cell
            stored, outcomes = record(tool, row, extra, index, warmup)
            cell['runs'].append(stored)
            if outcomes is not None:
                maps[(index, tool)] = outcomes
                cell['first_outcomes'].setdefault(tool, outcomes)
            print(f"  wall={stored['wall_seconds']:.2f}s cpu={stored['cpu_seconds']:.2f}s "
                  f"rss={stored['peak_aggregate_rss_bytes'] / 2**20:.0f}MiB mutants={stored.get('mutants')} "
                  f"counts={stored.get('counts')}", flush=True)
            shutil.rmtree(project_dir, ignore_errors=True)  # bulky and reproducible; reports/logs stay
    if {'oximut', 'stryker'} <= cell['first_outcomes'].keys():
        cell['scope_comparison'] = compare_scope(cell['first_outcomes']['oximut'], cell['first_outcomes']['stryker'])
    return cell


# --- planning --------------------------------------------------------------------------------

def parse_workers(text):
    """Space or comma separated worker counts, each 1-1024, deduplicated in order."""
    values = []
    for word in text.replace(',', ' ').split():
        if not word.isdigit() or not 1 <= int(word) <= 1024:
            raise BenchmarkError(f'worker count {word!r} must be an integer from 1 to 1024')
        if int(word) not in values:
            values.append(int(word))
    if not values:
        raise BenchmarkError('at least one worker count is required')
    return values


def plan_matrix(projects, workers):
    """GitHub Actions matrix: one cell per project and worker count."""
    return {'include': [{'project': project['name'], 'workers': count}
                        for project in projects for count in workers]}


def skipped_cell(args, project):
    return {'schemaVersion': SCHEMA_VERSION, 'status': 'skipped',
            'error': f'{args.workers} workers exceed the {usable_cpus()} usable CPUs: Oximut would cap its workers '
                     'and Stryker would not, so the cell is not a matched comparison',
            'project': {key: project[key] for key in ('name', 'kind', 'repository', 'commit', 'description')},
            'scope': args.scope, 'workers': args.workers, 'warmups': args.warmups, 'rounds': args.rounds,
            'machine': {'cpu_model': cpu_model(), 'usable_cpus': usable_cpus(), 'platform': platform.platform()},
            'runs': []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--projects', nargs='*', default=[], help='project names (default: all)')
    parser.add_argument('--list-projects', action='store_true', help='print project names as JSON and exit')
    parser.add_argument('--plan', metavar='WORKERS',
                        help='print the GitHub matrix for the selected projects and these worker counts, then exit')
    parser.add_argument('--workers', type=int, default=4, help='matched Stryker concurrency and Oximut --workers')
    parser.add_argument('--scope', choices=SCOPES, default='matched',
                        help='matched: Stryker limited to the operators matching Oximut\'s families; '
                             'full: every default operator of each tool')
    parser.add_argument('--stryker-related', action='store_true',
                        help='keep Stryker\'s vitest.related default (on); the harness turns it off, as the colord benchmark does')
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--rounds', type=int, default=3, help='measured rounds per tool')
    parser.add_argument('--timeout', type=float, default=1800, help='seconds per tool run')
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/release/oximut')
    parser.add_argument('--work-dir', type=Path, default=WORK_DIR)
    parser.add_argument('--output', type=Path, help='cell directory (must not exist)')
    parser.add_argument('--tools', nargs='+', choices=TOOLS, default=list(TOOLS))
    parser.add_argument('--count-mutants', action='store_true',
                        help='fetch the projects and print each one\'s `oximut --list` mutant count, then exit')
    parser.add_argument('--skip-oversubscribed', action='store_true',
                        help='record a skipped cell instead of failing when --workers exceeds the usable CPUs')
    args = parser.parse_args(argv)
    try:
        projects = select_projects(load_projects(), args.projects)
        if args.list_projects:
            print(json.dumps([project['name'] for project in projects]))
            return 0
        if args.plan is not None:
            print(json.dumps(plan_matrix(projects, parse_workers(args.plan))))
            return 0
        if args.count_mutants:
            for project in projects:
                checkout = fetch_checkout(project, args.work_dir)
                files = resolve_mutate(project, project_source(project, checkout))
                destination = args.work_dir / '_count' / project['name']
                shutil.rmtree(destination, ignore_errors=True)
                prepare_run(project, project_source(project, checkout), destination)
                result = run_parity.subprocess.run(
                    [str(args.binary), '--root', str(destination), '--config', CONFIG_NAME, '--list',
                     *[arg for name in files for arg in ('--source', name)]],
                    check=True, text=True, stdout=run_parity.subprocess.PIPE)
                print(f"{project['name']} ({project['kind']}): {len(files)} files, "
                      f"{len(json.loads(result.stdout)['mutants'])} Oximut mutants")
            return 0
        if len(projects) != 1:
            parser.error('--projects must name exactly one project to run a cell')
        if args.output is None:
            parser.error('--output is required to run a cell')
        if args.warmups < 0 or args.rounds < 1:
            parser.error('require --warmups >= 0 and --rounds >= 1')
        if not 1 <= args.workers <= 1024:
            parser.error('--workers must be between 1 and 1024')
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error('--timeout must be positive and finite')
        if len(set(args.tools)) != len(args.tools):
            parser.error('--tools must not repeat a tool')
        args.binary = args.binary.resolve()
        if 'oximut' in args.tools and not args.binary.is_file():
            parser.error(f'Oximut binary not found: {args.binary}')
        project = projects[0]
        args.output = args.output.resolve()
        args.output.mkdir(parents=True, exist_ok=False)
        if args.workers > usable_cpus():
            cell = skipped_cell(args, project)
            (args.output / 'cell.json').write_text(json.dumps(cell, indent=2) + '\n')
            print(cell['error'], flush=True)
            return 0 if args.skip_oversubscribed else 1
        checkout = fetch_checkout(project, args.work_dir)
        cell = run_cell(args, project, checkout)
    except BenchmarkError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    (args.output / 'cell.json').write_text(json.dumps(cell, indent=2, sort_keys=True) + '\n')
    print(f"cell {project['name']} x{args.workers}: {cell['status']}" + (f" ({cell['error']})" if cell['error'] else ''),
          flush=True)
    return 0 if cell['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
