#!/usr/bin/env python3
"""Matched, alternating fresh/persistent M2 timings; rejects outcome disagreements."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import time

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--warmups', type=int, default=1)
    args = parser.parse_args()
    if args.repetitions < 2 or args.warmups < 0:
        parser.error('at least two repetitions and nonnegative warmups required')
    args.output.mkdir(parents=True, exist_ok=False)
    binary = REPO / 'target/debug/oximut'
    metadata = {
        'platform': platform.platform(), 'architecture': platform.machine(),
        'node': subprocess.check_output(['node', '--version'], text=True).strip(),
        'rust': subprocess.check_output(['rustc', '--version'], text=True).strip(),
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO)),
        'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'repetitions': args.repetitions, 'warmups': args.warmups,
        'build': 'debug', 'workers': 1,
        'metric': 'end-to-end monotonic elapsed seconds; no CPU or memory claim',
        'implementationSha256': {
            str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [REPO / 'package-lock.json', REPO / 'Cargo.lock',
                         *sorted((REPO / 'packages/vitest-adapter').glob('*.mjs')),
                         *sorted((REPO / 'packages/vitest-adapter').glob('*.ts')),
                         REPO / 'packages/vitest-adapter/supervisor.py']
        },
    }
    (args.output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    rows = []
    expected = {}
    for repetition in range(-args.warmups, args.repetitions):
        for fixture, source in [('m1', 'subject.ts'), ('m2', 'subject.ts'), ('stateful', 'service.ts')]:
            modes = ['fresh', 'persistent'] if repetition % 2 == 0 else ['persistent', 'fresh']
            for mode in modes:
                root = REPO / 'fixtures' / fixture
                name = f'{repetition + args.warmups:02d}-{fixture}-{mode}'
                report_path = args.output.resolve() / f'{name}.json'
                command = [str(binary), '--root', str(root), '--config', str(root / 'vitest.config.ts'),
                           '--source', str(root / 'src' / source), '--execution', mode,
                           '--order-seed', str(100 + repetition), '--report', str(report_path)]
                started = time.monotonic()
                result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, timeout=180)
                elapsed = time.monotonic() - started
                (args.output / f'{name}.log').write_text(result.stdout + result.stderr)
                report = json.loads(report_path.read_text())
                outcomes = {m['id']: m['outcome'] for m in report['mutants']}
                if (result.returncode not in (0, 1) or report['baseline']['outcome'] != 'passed'
                        or not report['summary']['complete']):
                    raise RuntimeError(f'{name}: incomplete run; cannot compare performance')
                if outcomes != expected.setdefault(fixture, outcomes):
                    raise RuntimeError(f'{name}: outcome disagreement; cannot compare performance')
                row = {'fixture': fixture, 'execution': mode, 'repetition': repetition,
                       'warmup': repetition < 0, 'elapsedSeconds': elapsed,
                       'mutants': len(outcomes), 'report': report_path.name, 'command': command}
                rows.append(row)
                with (args.output / 'measurements.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
                print(f'{name}: {elapsed:.3f}s', flush=True)
    summary = {}
    for fixture in expected:
        timings = {mode: [r['elapsedSeconds'] for r in rows if r['fixture'] == fixture
                         and r['execution'] == mode and not r['warmup']]
                   for mode in ('fresh', 'persistent')}
        medians = {mode: statistics.median(values) for mode, values in timings.items()}
        summary[fixture] = {'samples': timings, 'medians': medians,
                            'freshOverPersistent': medians['fresh'] / medians['persistent'],
                            'outcomesAgree': True}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
