#!/usr/bin/env python3
"""Serial, correctness-gated before/after campaigns on the pinned colord workload.

Build both candidates first. This command never installs, builds or retries runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import m6
import real_project as workload
import targets

BASELINE = '4a6993b564758d15d976cd0998ba490fbf64e1e1'
METRICS = ('wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes')
VARIANTS = ('before', 'after')


def plan(warmups, repetitions):
    for index in range(warmups + repetitions):
        for variant in VARIANTS if index % 2 == 0 else reversed(VARIANTS):
            yield index, index < warmups, variant, (
                ['vitest', 'oximut', 'stryker'] if index % 2 == 0 else
                ['stryker', 'oximut', 'vitest'])


def evaluate(rows, warmups, repetitions, provenance, failures=()):
    """No ratios until the entire campaign, including every warmup, qualifies."""
    errors = list(failures)
    manifest = workload.measurement.read_json(workload.CORPUS / 'workload.json')
    expected_inventory = m6.digest(sorted(manifest['expectedTests']))
    indexed = {}
    for row in rows:
        key = (row.get('campaign_round'), row.get('variant'), row.get('tool'))
        if key in indexed:
            errors.append(f'duplicate campaign row: {key}')
        indexed[key] = row
    expected = {(index, variant, tool) for index in range(warmups + repetitions)
                for variant in VARIANTS for tool in ('vitest', 'oximut', 'stryker')}
    if set(indexed) != expected or len(rows) != len(expected):
        errors.append('missing, duplicate or unexpected round/variant/tool evidence')
    if warmups < 1 or repetitions < 5:
        errors.append('require at least one warmup and five measured pairs')
    reference = None
    for index in range(warmups + repetitions):
        for variant in VARIANTS:
            group = [indexed.get((index, variant, tool)) for tool in ('vitest', 'oximut', 'stryker')]
            if any(row is None for row in group):
                continue
            comparison, _ = workload.compare_rows(group)
            label = f'round {index} {variant}'
            if len(comparison) != 1 or not comparison[0]['ratio_eligible']:
                errors.append(f'{label}: failed exact workload/test/measurement gate')
            for row in group:
                if row.get('warmup') != (index < warmups):
                    errors.append(f'{label}: inconsistent warmup label')
                if row.get('round') != 0:
                    errors.append(f'{label}: unexpected subcampaign round')
                if row.get('workload_sha256') != m6.digest(manifest):
                    errors.append(f'{label}: changed declared workload')
                if row.get('declared_test_identity_sha256') != expected_inventory:
                    errors.append(f'{label}: unequal declared test inventory')
                if row.get('mutator_families') != manifest['families']:
                    errors.append(f'{label}: changed declared mutant families')
                if row.get('execution_inputs') != provenance[variant]:
                    errors.append(f'{label}: binary or adapter provenance changed')
                if row['tool'] != 'vitest':
                    if reference is None:
                        reference = row
                    elif not m6.compare(reference, row)['ratio_eligible']:
                        errors.append(f'{label}: mutation identities/outcomes changed across candidates/rounds')
    phases = {variant: [
        {'round': row.get('campaign_round'), 'warmup': row.get('warmup'),
         'report_file': row.get('report_file'),
         'phaseWallMs': (row.get('resources') or {}).get('phaseWallMs'),
         'disabledValidationRuns': (row.get('resources') or {}).get('disabledValidationRuns')}
        for row in rows if row.get('variant') == variant and row.get('tool') == 'oximut']
        for variant in VARIANTS}
    result = {'phase_evidence': phases, 'correctness_gate': {'passed': not errors, 'errors': errors},
              'warmup_pairs': warmups, 'measured_pairs': repetitions,
              'thresholds': targets.THRESHOLDS, 'targets_met': False, 'comparisons': {},
              'scope': 'Five declared production mutation files, all 41 source files available, '
                       'all 102 upstream tests; not the full colord mutation scope.',
              'confidence': 'Deterministic paired median percentile bootstrap, 10000 draws; '
                            'host/corpus-specific exploratory evidence.'}
    if errors:
        return result
    for name, numerator_variant, numerator_tool, denominator_variant, denominator_tool in (
        ('before_vs_stryker', 'before', 'oximut', 'before', 'stryker'),
        ('after_vs_stryker', 'after', 'oximut', 'after', 'stryker'),
        ('after_vs_before', 'after', 'oximut', 'before', 'oximut'),
    ):
        metrics = {}
        for metric in METRICS:
            pairs = [(indexed[(i, numerator_variant, numerator_tool)][metric],
                      indexed[(i, denominator_variant, denominator_tool)][metric])
                     for i in range(warmups, warmups + repetitions)]
            value = targets.paired_interval(pairs)
            if metric in targets.THRESHOLDS and name != 'after_vs_before':
                threshold = targets.THRESHOLDS[metric]
                lo, hi = value['bootstrap_95_percent']
                value.update(target_ratio=threshold, median_target_met=value['median'] <= threshold,
                             status='target met' if hi <= threshold else
                                    'target missed' if lo > threshold else 'inconclusive')
            if metric == 'cpu_seconds':
                value['median_cpu_reduction_percent'] = 100 * (1 - value['median'])
            metrics[metric] = value
        result['comparisons'][name] = {
            'numerator': f'{numerator_variant}/{numerator_tool}',
            'denominator': f'{denominator_variant}/{denominator_tool}', 'metrics': metrics}
    primary = result['comparisons']['after_vs_stryker']['metrics']
    result['targets_met'] = all(primary[metric]['status'] == 'target met' for metric in targets.THRESHOLDS)
    return result


def subcommand(args, directory, warmup, variant, tools):
    source = getattr(args, f'{variant}_adapter_dir')
    return [sys.executable, str(workload.ROOT / 'benchmarks/real_project.py'),
            '--binary', str(getattr(args, f'{variant}_binary')),
            '--adapter', str(source / 'm2-runner.mjs'), '--adapter-source-dir', str(source),
            '--defer-ratios', '--tools', *tools, '--warmups', '1' if warmup else '0',
            '--repetitions', '0' if warmup else '1', '--timeout', str(args.timeout),
            '--output', str(directory)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    for variant in VARIANTS:
        parser.add_argument(f'--{variant}-binary', type=Path, required=True)
        parser.add_argument(f'--{variant}-adapter-dir', type=Path, required=True)
        parser.add_argument(f'--{variant}-commit', default=BASELINE if variant == 'before' else None,
                            required=variant == 'after')
    parser.add_argument('--build-flags', default='--locked --release -p oximut-cli')
    parser.add_argument('--build-metadata', type=Path, help='Additional build provenance JSON from workflow')
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--require-targets', action='store_true')
    args = parser.parse_args(argv)
    if args.warmups < 1 or args.repetitions < 5:
        parser.error('require at least one warmup and five measured rounds')
    provenance = {}
    for variant in VARIANTS:
        commit = getattr(args, f'{variant}_commit')
        if not re.fullmatch('[0-9a-f]{40}', commit):
            parser.error(f'--{variant}-commit requires a full lowercase commit SHA')
        binary = getattr(args, f'{variant}_binary').resolve()
        adapter = getattr(args, f'{variant}_adapter_dir').resolve()
        if not binary.is_file() or not (adapter / 'm2-runner.mjs').is_file():
            parser.error(f'{variant}: build binary and supply matching adapter directory before timing')
        setattr(args, f'{variant}_binary', binary)
        setattr(args, f'{variant}_adapter_dir', adapter)
        provenance[variant] = workload.execution_inputs(SimpleNamespace(
            binary=binary, adapter=adapter / 'm2-runner.mjs', adapter_source_dir=adapter))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = workload.measurement.metadata()
    metadata.update(before_commit=args.before_commit, after_commit=args.after_commit,
                    build_flags=args.build_flags, runtime_provenance=provenance,
                    build_metadata=workload.measurement.read_json(args.build_metadata)
                                   if args.build_metadata else None,
                    policy='Both binaries built before timing; serial subcampaigns; no installs, builds '
                           'or automatic retries. Matching adapters must remain unchanged. '
                           'Caller must keep this host free of other benchmark/build work.',
                    warmups=args.warmups, repetitions=args.repetitions,
                    harness_sha256={name: workload.measurement.file_hash(workload.ROOT / 'benchmarks' / name)
                                    for name in ('compare_candidates.py', 'real_project.py', 'targets.py', 'm6.py', 'run.py')})
    m6.write_json(output / 'metadata.json', metadata)
    failures, rows, attempts = [], [], []
    with (output / 'measurements.jsonl').open('w') as stream:
        for index, warmup, variant, tools in plan(args.warmups, args.repetitions):
            directory = output / f'{index:02d}-{variant}'
            cmd = subcommand(args, directory, warmup, variant, tools)
            attempt = {'round': index, 'variant': variant, 'warmup': warmup, 'command': cmd}
            print(f'round {index}: {variant} ({"warmup" if warmup else "measured"})', flush=True)
            try:
                # The orchestration process is deliberately outside run.py timing/accounting.
                with (output / f'{index:02d}-{variant}.log').open('w') as log:
                    completed = subprocess.run(cmd, cwd=workload.ROOT, stdout=log, stderr=subprocess.STDOUT)
                attempt['exit_code'] = completed.returncode
                if completed.returncode:
                    failures.append(f'round {index} {variant}: subcampaign exited {completed.returncode}')
                meta = workload.measurement.read_json(directory / 'metadata.json')
                if meta['versions'] != metadata['versions']:
                    failures.append(f'round {index} {variant}: tool versions changed')
                for line in (directory / 'measurements.jsonl').read_text().splitlines():
                    row = json.loads(line)
                    row.update(campaign_round=index, variant=variant, subcampaign=str(directory))
                    rows.append(row)
                    stream.write(json.dumps(row, sort_keys=True) + '\n')
                    stream.flush()
            except (OSError, ValueError, KeyError) as error:
                attempt['error'] = str(error)
                failures.append(f'round {index} {variant}: {error}')
            attempts.append(attempt)
            m6.write_json(output / 'attempts.json', attempts)
    result = evaluate(rows, args.warmups, args.repetitions, provenance, failures)
    m6.write_json(output / 'summary.json', result)
    print(json.dumps(result, indent=2))
    return 1 if not result['correctness_gate']['passed'] or (args.require_targets and not result['targets_met']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
