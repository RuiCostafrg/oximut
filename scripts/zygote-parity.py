#!/usr/bin/env python3
"""Run fixtures/m1 persistently with --zygote and with --no-zygote and require identical results.

Fails unless both runs are complete with the same exit code and mutant outcomes,
and the zygote run's counters show forks and no zygote failures.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COUNTER_KEYS = ('zygotes', 'forks', 'exits', 'zygoteFailures', 'fallbacks')


def run(binary, root, extra, directory, label, timeout):
    report = directory / f'{label}.json'
    counters = directory / f'{label}-counters'
    counters.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith('OXIMUT_ZYGOTE')}
    env['OXIMUT_ZYGOTE_COUNTERS_DIR'] = str(counters)
    command = [str(binary), '--root', str(root), '--source', str(root / 'src/subject.ts'),
               '--execution', 'persistent', '--workers', '2', '--report', str(report), *extra]
    result = subprocess.run(command, env=env, cwd=ROOT, text=True, timeout=timeout,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = json.loads(report.read_text())
    totals = {key: 0 for key in COUNTER_KEYS}
    files = sorted(counters.iterdir())
    for file in files:
        for key, value in json.loads(file.read_text()).items():
            if key in totals:
                totals[key] += value
    return result, data, totals, len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/oximut')
    parser.add_argument('--root', type=Path, default=ROOT / 'fixtures/m1')
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='oximut-zygote-parity-') as tmp:
        directory = Path(tmp)
        plain, plain_report, _, plain_files = run(args.binary, args.root, ['--no-zygote'], directory, 'plain', args.timeout)
        zygote, zygote_report, counters, files = run(args.binary, args.root, ['--zygote'], directory, 'zygote', args.timeout)
    outcomes = lambda report: {m['id']: m['outcome'] for m in report['mutants']}
    problems = []
    if plain.returncode != zygote.returncode:
        problems.append(f'exit codes differ: {plain.returncode} vs {zygote.returncode}')
    for name, report in (('plain', plain_report), ('zygote', zygote_report)):
        if report['summary'].get('complete') is not True:
            problems.append(f'{name} run is not complete: {report["summary"]}')
    if not outcomes(plain_report) or outcomes(plain_report) != outcomes(zygote_report):
        problems.append(f'mutant outcomes differ: {outcomes(plain_report)} vs {outcomes(zygote_report)}')
    if plain_files:
        problems.append('the run without --zygote wrote zygote counters')
    if not files or counters['forks'] <= 0:
        problems.append(f'zygote forked nothing: {counters}')
    if counters['zygoteFailures'] != 0:
        problems.append(f'zygote failures: {counters}')
    if not any('zygote=on' in d for d in zygote_report.get('diagnostics', [])):
        problems.append('zygote report lacks the zygote=on diagnostic')
    if problems:
        for problem in problems:
            print('FAIL:', problem, file=sys.stderr)
        print(plain.stderr[-2000:], zygote.stderr[-2000:], file=sys.stderr)
        return 1
    print(f'zygote parity ok: {len(outcomes(zygote_report))} mutants identical, exit {zygote.returncode}, '
          f'complete=true in both, counters {counters}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
