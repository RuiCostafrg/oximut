#!/usr/bin/env python3
"""Serial, correctness-gated before/after campaigns on the pinned colord workload.

Build both candidates first. This command never installs, builds or retries runs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import m6
import real_project as workload
import targets

BASELINE = '4a6993b564758d15d976cd0998ba490fbf64e1e1'
METRICS = ('wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes')
VARIANTS = ('before', 'after')
PHASES = ('inputAndSchedulerPreparation', 'originalBaseline', 'disabledSwitchValidation',
          'coverageBaseline', 'postBaselineValidation', 'mutationPlanning',
          'mutationExecution', 'teardown', 'finalInputValidation')


class CleanupError(subprocess.SubprocessError):
    pass


def descendants(root):
    snapshot = workload.measurement.process_snapshot()
    found = {}
    while True:
        children = {pid: stat for pid, stat in snapshot.items()
                    if stat['ppid'] in found or stat['ppid'] == root}
        if len(children) == len(found):
            return found
        found = children


def reap_descendants(bound=30):
    """Kill everything left under this process; True if anything was still alive."""
    found = False
    deadline = time.monotonic() + bound
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            # As a subreaper, any live descendant keeps some child of ours alive: ECHILD means none remain.
            return found
        for pid, stat in descendants(os.getpid()).items():
            found = True
            try:  # Avoid signaling a reused PID between sampling and killing.
                current = workload.measurement.parse_proc_stat(Path(f'/proc/{pid}/stat').read_text())
                if current['starttime'] == stat['starttime']:
                    os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, FileNotFoundError):
                pass
        time.sleep(.02)
    raise CleanupError(f'descendant cleanup incomplete after {bound}s')


def run_subcampaign(cmd, cwd, log, timeout, attempt=None, bound=30):
    """Run one subcampaign; its detached tool sessions never outlive it (even on timeout)."""
    attempt = {} if attempt is None else attempt
    workload.measurement.enable_subreaper()
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    expired = None
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired as error:
        expired = error
    finally:
        # Also on interruption: detached tool sessions would otherwise keep running.
        if proc.poll() is None:
            proc.kill()
            try:  # A root stuck in an uninterruptible wait must not hang the campaign.
                proc.wait(bound)
            except subprocess.TimeoutExpired:
                attempt['descendant_cleanup_required'] = True
                try:  # Still signal the detached tools; ECHILD cannot occur while the root lives.
                    reap_descendants(bound)
                except CleanupError:
                    pass
                raise CleanupError(f'subcampaign root did not exit within {bound}s of SIGKILL') from None
        try:
            attempt['descendant_cleanup_required'] = reap_descendants(bound)
        except CleanupError:
            attempt['descendant_cleanup_required'] = True
            raise
    if expired:
        raise expired
    return proc.returncode


def build_errors(provenance, metadata, commits, required):
    errors = []
    if required and provenance['before']['binary_sha256'] == provenance['after']['binary_sha256']:
        errors.append('expected distinct candidate executables but binary hashes are identical')
    if metadata is None and not required:
        return errors
    if not isinstance(metadata, dict):
        return errors + ['missing or malformed build metadata']
    for variant in VARIANTS:
        if metadata.get(f'{variant}_commit') != commits.get(variant):
            errors.append(f'{variant}: build commit does not match requested candidate')
        hashes = metadata.get('binary_sha256')
        if not isinstance(hashes, dict) or hashes.get(variant) != provenance[variant]['binary_sha256']:
            errors.append(f'{variant}: build hash does not match observed executable')
    if required:
        directories = metadata.get('cargo_target_dirs')
        if not isinstance(directories, dict) or any(
                not isinstance(directories.get(v), str) or not Path(directories[v]).is_absolute()
                for v in VARIANTS):
            errors.append('missing absolute isolated Cargo target directories')
        else:
            before, after = (Path(directories[v]).resolve() for v in VARIANTS)
            if before.is_relative_to(after) or after.is_relative_to(before):
                errors.append('candidate Cargo target directories overlap')
    return errors


def valid_duration(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def phase_errors(row, manifest):
    resources = row.get('resources')
    if not isinstance(resources, dict):
        return ['missing or malformed after resources']
    phases = resources.get('phaseWallMs')
    errors = []
    if not isinstance(phases, dict) or any(not valid_duration(phases.get(key)) for key in PHASES):
        errors.append('missing, incomplete or invalid after phase timings')
    runs = resources.get('disabledValidationRuns')
    if not isinstance(runs, list):
        return errors + ['missing or malformed disabled validation records']
    files = []
    for run in runs:
        if not isinstance(run, dict) or not valid_duration(run.get('wallMs')):
            errors.append('invalid disabled validation duration')
            continue
        try:
            file = Path(run['file']).relative_to(Path(row['cwd'])).as_posix()
        except (KeyError, TypeError, ValueError):
            errors.append('invalid disabled validation file')
            continue
        files.append(file)
    if sorted(files) != sorted(manifest['mutate']):
        errors.append('missing, duplicate or unexpected disabled validation files')
    return errors


def plan(warmups, repetitions):
    for index in range(warmups + repetitions):
        for variant in VARIANTS if index % 2 == 0 else reversed(VARIANTS):
            yield index, index < warmups, variant, (
                ['vitest', 'oximut', 'stryker'] if index % 2 == 0 else
                ['stryker', 'oximut', 'vitest'])


def evaluate(rows, warmups, repetitions, provenance, failures=(), *,
             require_build_integrity=False, build_metadata=None, candidate_commits=None):
    """No ratios until the entire campaign, including every warmup, qualifies."""
    integrity_errors = build_errors(provenance, build_metadata, candidate_commits or {},
                                   require_build_integrity)
    errors = list(failures) + integrity_errors
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
                if require_build_integrity and variant == 'after' and row['tool'] == 'oximut':
                    errors.extend(f'{label}: {error}' for error in phase_errors(row, manifest))
                if row['tool'] != 'vitest':
                    if reference is None:
                        reference = row
                    elif not m6.compare(reference, row)['ratio_eligible']:
                        errors.append(f'{label}: mutation identities/outcomes changed across candidates/rounds')
    phases = {variant: [
        {'round': row.get('campaign_round'), 'warmup': row.get('warmup'),
         'report_file': row.get('report_file'),
         'phaseWallMs': (row['resources'] if isinstance(row.get('resources'), dict) else {}).get('phaseWallMs'),
         'disabledValidationRuns': (row['resources'] if isinstance(row.get('resources'), dict) else {}).get('disabledValidationRuns')}
        for row in rows if row.get('variant') == variant and row.get('tool') == 'oximut']
        for variant in VARIANTS}
    result = {'phase_evidence': phases, 'correctness_gate': {'passed': not errors, 'errors': errors},
              'build_integrity': {'required': require_build_integrity,
                                  'passed': not integrity_errors, 'errors': integrity_errors},
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


def stop_on_sigterm(signum, _frame):
    # Unwind like SIGINT so run_subcampaign kills and reaps the active subcampaign.
    raise SystemExit(128 + signum)


def main(argv=None):
    signal.signal(signal.SIGTERM, stop_on_sigterm)
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
    parser.add_argument('--require-build-integrity', action='store_true',
                        help='Pinned CI comparison: isolated builds, distinct binaries and complete after phase evidence')
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive and finite')
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
    build_metadata, build_metadata_error = None, None
    if args.build_metadata:
        try:
            build_metadata = workload.measurement.read_json(args.build_metadata)
        except (OSError, ValueError) as error:
            build_metadata_error = f'cannot read supplied build metadata: {error}'
    metadata = workload.measurement.metadata()
    metadata.update(before_commit=args.before_commit, after_commit=args.after_commit,
                    build_flags=args.build_flags, runtime_provenance=provenance,
                    build_metadata=build_metadata, build_metadata_error=build_metadata_error,
                    build_metadata_path=str(args.build_metadata) if args.build_metadata else None,
                    policy='Both binaries built before timing; serial subcampaigns; no installs, builds '
                           'or automatic retries. Matching adapters must remain unchanged. '
                           'Caller must keep this host free of other benchmark/build work.',
                    warmups=args.warmups, repetitions=args.repetitions,
                    require_build_integrity=args.require_build_integrity,
                    harness_sha256={name: workload.measurement.file_hash(workload.ROOT / 'benchmarks' / name)
                                    for name in ('compare_candidates.py', 'real_project.py', 'targets.py', 'm6.py', 'run.py')})
    m6.write_json(output / 'metadata.json', metadata)
    failures, rows, attempts = [], [], []
    integrity = dict(require_build_integrity=args.require_build_integrity,
                     build_metadata=metadata['build_metadata'],
                     candidate_commits={'before': args.before_commit, 'after': args.after_commit})
    preflight = build_errors(provenance, metadata['build_metadata'], integrity['candidate_commits'],
                             args.require_build_integrity)
    if build_metadata_error:
        preflight.append(build_metadata_error)
    if preflight:
        (output / 'measurements.jsonl').touch()
        m6.write_json(output / 'attempts.json', [])
        m6.write_json(output / 'summary.json', evaluate([], args.warmups, args.repetitions,
                                                      provenance, [build_metadata_error] if build_metadata_error else [],
                                                      **integrity))
        print('Build integrity preflight failed: ' + '; '.join(preflight), file=sys.stderr)
        return 1
    with (output / 'measurements.jsonl').open('w') as stream:
        for index, warmup, variant, tools in plan(args.warmups, args.repetitions):
            directory = output / f'{index:02d}-{variant}'
            cmd = subcommand(args, directory, warmup, variant, tools)
            attempt = {'round': index, 'variant': variant, 'warmup': warmup, 'command': cmd}
            print(f'round {index}: {variant} ({"warmup" if warmup else "measured"})', flush=True)
            try:
                # The orchestration process is deliberately outside run.py timing/accounting.
                with (output / f'{index:02d}-{variant}.log').open('w') as log:
                    returncode = run_subcampaign(cmd, workload.ROOT, log, len(tools) * args.timeout + 300, attempt)
                attempt['exit_code'] = returncode
                if returncode:
                    failures.append(f'round {index} {variant}: subcampaign exited {returncode}')
                if attempt.get('descendant_cleanup_required'):
                    # Survivors ran outside per-tool accounting and may have skewed this round.
                    failures.append(f'round {index} {variant}: descendants outlived the subcampaign root')
                meta = workload.measurement.read_json(directory / 'metadata.json')
                if not isinstance(meta, dict) or not isinstance(meta.get('versions'), dict):
                    raise ValueError('subcampaign metadata lacks a versions object')
                if meta['versions'] != metadata['versions']:
                    failures.append(f'round {index} {variant}: tool versions changed')
                for line in (directory / 'measurements.jsonl').read_text().splitlines():
                    row = json.loads(line)
                    row.update(campaign_round=index, variant=variant, subcampaign=str(directory))
                    rows.append(row)
                    stream.write(json.dumps(row, sort_keys=True) + '\n')
                    stream.flush()
            except (OSError, ValueError, KeyError, TypeError, AttributeError,
                    subprocess.SubprocessError) as error:
                attempt['error'] = str(error)
                if isinstance(error, subprocess.TimeoutExpired):
                    attempt['timed_out'] = True
                if isinstance(error, CleanupError):
                    attempt['timed_out'] = True  # Unkillable leftovers would skew later rounds: stop.
                failures.append(f'round {index} {variant}: {error}')
            attempts.append(attempt)
            m6.write_json(output / 'attempts.json', attempts)
            if attempt.get('timed_out'):
                break
    result = evaluate(rows, args.warmups, args.repetitions, provenance, failures, **integrity)
    m6.write_json(output / 'summary.json', result)
    print(json.dumps(result, indent=2))
    return 1 if not result['correctness_gate']['passed'] or (args.require_targets and not result['targets_met']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
