#!/usr/bin/env python3
"""M6 correctness-gated fixture measurements (Linux, Python standard library).

Build/install before running. Each scenario uses a private fixture copy. Raw
reports, bounded logs, setup runs and all measurements survive gate failures.
Cross-tool ratios require exact mutation-set equality; overlap is diagnostic.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import run as measurement

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
FIXTURES = {
    'pure': ('logic.ts',),
    'stateful': ('dependency.ts', 'service.ts', 'state.ts'),
    'm1': ('subject.ts',),
    'm2': ('subject.ts',),
}
TEST_COUNTS = {'pure': 7, 'stateful': 4, 'm1': 2, 'm2': 4}
CHANGES = ('source', 'test', 'helper', 'snapshot', 'config', 'lockfile', 'environment')
SENTINELS = {
    'm1': ('subject.ts', 'return value < 10;', 'return value <= 10;'),
    'm2': ('subject.ts', 'return value < 3;', 'return value <= 3;'),
    'pure': ('logic.ts', 'if (score >= 90)', 'if (score > 90)'),
    'stateful': ('service.ts', 'value + adjustment <= limit', 'value + adjustment < limit'),
}
METRICS = ('wall_seconds', 'cpu_seconds', 'peak_aggregate_rss_bytes', 'peak_process_count')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def offset(text, position):
    """Convert one-based line/UTF16 column (both report schemas) to Python index."""
    line, column = position['line'], position['column']
    lines = text.splitlines(keepends=True)
    if not isinstance(line, int) or not isinstance(column, int) or line < 1 or column < 1:
        raise ValueError('invalid source position')
    if line == len(lines) + 1 and column == 1:
        return len(text)
    if line > len(lines):
        raise ValueError('source position outside file')
    units = 0
    for index, char in enumerate(lines[line - 1]):
        if units == column - 1:
            return sum(map(len, lines[:line - 1])) + index
        units += len(char.encode('utf-16-le')) // 2
    if units == column - 1:
        return sum(map(len, lines[:line]))
    raise ValueError('source position outside line or splitting a UTF16 character')


def mutation_identity(filename, source, location, replacement):
    """Exact edited-file identity: ignores tool IDs and expression/operator spans.

    Parentheses and formatting differences remain exclusions; no semantic
    equivalence is guessed. Unicode and whitespace remain significant.
    """
    start, end = offset(source, location['start']), offset(source, location['end'])
    if start > end or not isinstance(replacement, str):
        raise ValueError('invalid mutation edit')
    edited = source[:start] + replacement + source[end:]
    return filename + ':' + hashlib.sha256(edited.encode()).hexdigest()


def validate_oximut_report(report, path):
    """Validate the consumed snapshot with the shared schema, outside timings."""
    validator = (ROOT / 'scripts/validate-reports.mjs').as_uri()
    script = (
        'import { readFileSync } from "node:fs"; '
        f'import {{ validateReport }} from {json.dumps(validator)}; '
        'try { validateReport(JSON.parse(readFileSync(0, "utf8")), process.argv[1]); } '
        'catch (error) { console.error(error.message); process.exitCode = 1; }'
    )
    try:
        result = subprocess.run(
            ['node', '--input-type=module', '-e', script, '--', str(path)],
            input=json.dumps(report), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'Oximut report schema validation unavailable: {error}') from error
    if result.returncode != 0:
        raise ValueError(f'Oximut report schema validation failed: {result.stderr.strip()}')


def artifact(tool, path, project, row):
    """Validate reports independently of exit status; expose reproducible identities."""
    allowed_exit = (0, 1) if tool == 'oximut' else (0,)
    if (row.get('exit_code') not in allowed_exit or row.get('timed_out')
            or row.get('descendant_cleanup_required') or row.get('cleanup_error')
            or row.get('spawn_error')):
        raise ValueError('command failed, timed out, or required descendant cleanup')
    report = measurement.read_json(path)
    scope_exclusions = []
    fixture = row.get('fixture')
    if tool == 'vitest':
        validated = measurement.validate_artifact(tool, path, True)
        identities = []
        for suite in report.get('testResults', []):
            filename = Path(suite['name'])
            if filename.is_absolute():
                filename = filename.relative_to(project)
            identities.extend((filename.as_posix(), test['fullName'], test['status'])
                              for test in suite.get('assertionResults', []))
        validated['test_identity_sha256'] = digest(sorted(identities))
        expected_files = {p.relative_to(project).as_posix() for p in (project / 'test').rglob('*.test.ts')}
        if fixture and (report['numTotalTests'] != TEST_COUNTS[fixture]
                        or {identity[0] for identity in identities} != expected_files):
            raise ValueError('ordinary test count/files differ from fixture scope')
        return validated
    if tool == 'oximut':
        validate_oximut_report(report, path)
        baseline = report.get('baseline', {})
        tests = baseline.get('tests', {})
        if (report.get('schemaVersion') != 1 or baseline.get('outcome') != 'passed'
                or tests.get('passed', 0) < 1 or tests.get('failed', 0) != 0 or tests.get('skipped', 0) != 0
                or not report.get('summary', {}).get('complete')):
            raise ValueError('Oximut baseline failed/empty or mutation report incomplete')
        raw = report.get('mutants', [])
        if report['summary'].get('total') != len(raw):
            raise ValueError('Oximut summary does not match actual mutant count')
        if fixture and tests.get('passed') != TEST_COUNTS[fixture]:
            raise ValueError('Oximut baseline count differs from fixture scope')
        mutants = []
        for mutant in raw:
            filename = Path(mutant['file']).relative_to(project).as_posix()
            source = (project / filename).read_text()
            mutants.append((filename, source, mutant, mutant['outcome']))
        extra = {'report_run_id': report.get('runId'), 'cache': report.get('cache'), 'resources': report.get('resources'),
                 'cache_provenance_count': sum(m.get('provenance', {}).get('source') == 'cache' for m in raw),
                 'baseline_test_counts': tests, 'diagnostics': report.get('diagnostics', []),
                 'mutation_summary': report['summary'],
                 'selection_mode_counts': dict(collections.Counter(
                     m.get('selection', {}).get('mode', 'disabled') for m in raw)),
                 'selection_fallback_counts': dict(collections.Counter(
                     reason for m in raw for reason in m.get('selection', {}).get('reasons', []))),
                 'selected_tests_sum': sum(m.get('selection', {}).get('selectedTests', 0) for m in raw),
                 'reported_tests_sum_including_cached': sum(sum(m.get('tests', {}).values()) for m in raw)}
    else:
        # Includes zero-test classifications and infrastructure-status rejection.
        extra = measurement.validate_artifact(tool, path, True)
        if not report.get('testFiles'):
            raise ValueError('Stryker discovered no test identities')
        expected_files = {p.relative_to(project).as_posix() for p in (project / 'test').rglob('*.test.ts')}
        if fixture and (extra['test_counts']['numTotalTests'] != TEST_COUNTS[fixture]
                        or set(report['testFiles']) != expected_files):
            raise ValueError('Stryker discovered test count/files differ from fixture scope')
        mutants = []
        status = {'Killed': 'killed', 'Survived': 'survived', 'NoCoverage': 'no-coverage',
                  'Timeout': 'timeout', 'CompileError': 'invalid', 'Ignored': 'skipped'}
        for filename, data in report['files'].items():
            relative = Path(filename)
            if relative.is_absolute():
                relative = relative.relative_to(project)
            source = (project / relative).read_text()
            if data.get('source') != source:
                raise ValueError('Stryker report source does not match measured fixture')
            for mutant in data.get('mutants', []):
                if mutant['status'] == 'Ignored':
                    name = mutant.get('mutatorName')
                    reason = mutant.get('statusReason')
                    configured = report.get('config', {}).get('mutator', {}).get('excludedMutations', [])
                    if name not in configured or reason != f'Ignored because of excluded mutation \"{name}\"':
                        raise ValueError('unexplained ignored mutant; only explicit configured operator exclusions are allowed')
                    scope_exclusions.append({'identity': mutation_identity(relative.as_posix(), source,
                        mutant['location'], mutant['replacement']), 'file': relative.as_posix(),
                        'id': mutant['id'], 'operator': name, 'status': 'Ignored', 'reason': reason,
                        'location': mutant['location'], 'replacement': mutant['replacement']})
                    continue
                mutants.append((relative.as_posix(), source, mutant, status[mutant['status']]))
    identities = {}
    for filename, source, mutant, outcome in mutants:
        if outcome not in ('killed', 'survived', 'no-coverage'):
            raise ValueError(f'incomplete/non-comparable mutation outcome: {outcome}')
        if (tool == 'oximut' and outcome in ('killed', 'survived')
                and mutant.get('provenance', {}).get('source') != 'cache'
                and sum(mutant.get('tests', {}).get(k, 0) for k in ('passed', 'failed')) < 1):
            raise ValueError('Oximut classified a mutant without tests')
        if tool == 'oximut' and outcome == 'no-coverage':
            selection = mutant.get('selection') or {}
            if (selection.get('mode') != 'none'
                    or 'complete-baseline-without-hits' not in selection.get('reasons', [])):
                raise ValueError('no-coverage lacks complete attribution evidence')
        identity = mutation_identity(filename, source, mutant['location'], mutant['replacement'])
        if identity in identities:
            raise ValueError('duplicate exact edited-file identity; ambiguous workload')
        identities[identity] = {'id': mutant['id'], 'outcome': outcome,
                                'file': filename, 'location': mutant['location'],
                                'replacement': mutant['replacement'],
                                'operator': mutant.get('operator', mutant.get('mutatorName')),
                                'selection': mutant.get('selection')}
    if not identities:
        raise ValueError('empty mutation workload')
    sentinel = None
    if fixture:
        filename, original, replacement = SENTINELS[fixture]
        filename = 'src/' + filename
        source = (project / filename).read_text()
        if source.count(original) != 1:
            raise ValueError('known boundary mutation sentinel is absent or ambiguous')
        edited = source.replace(original, replacement, 1)
        sentinel = filename + ':' + hashlib.sha256(edited.encode()).hexdigest()
        if identities.get(sentinel, {}).get('outcome') != 'killed':
            raise ValueError('known shared boundary mutation sentinel was not killed')
    return {**extra, 'mutant_count': len(identities),
            'known_killed_sentinel_identity': sentinel,
            'generated_mutant_count': len(identities) + len(scope_exclusions),
            'explicitly_excluded_mutant_count': len(scope_exclusions),
            'scope_exclusions': scope_exclusions,
            'generated_mutation_status_counts': extra.get('mutation_status_counts'),
            'mutation_status_counts': dict(collections.Counter(m['outcome'] for m in identities.values())),
            'mutations': identities,
            'mutation_identity_outcome_sha256': digest({k: v['outcome'] for k, v in identities.items()})}


def compare(left, right, allow_uncovered=False):
    """Keep all exclusions/mismatches; a shared subset never permits a full-run ratio."""
    a, b = left.get('mutations', {}), right.get('mutations', {})
    shared = sorted(a.keys() & b.keys())
    mismatches, refinements = [], []
    for identity in shared:
        first, second = a[identity], b[identity]
        if first['outcome'] == second['outcome']:
            continue
        # Sound selection can refine a full-suite survivor into no-coverage.
        selection = second.get('selection') or {}
        if (allow_uncovered and first['outcome'] == 'survived'
                and second['outcome'] == 'no-coverage' and selection.get('mode') == 'none'
                and 'complete-baseline-without-hits' in selection.get('reasons', [])):
            refinements.append(identity)
        else:
            mismatches.append({'identity': identity, 'left': first['outcome'], 'right': second['outcome']})
    only_a, only_b = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
    return {'left_count': len(a), 'right_count': len(b), 'shared_count': len(shared),
            'shared': shared, 'left_only': {k: a[k] for k in only_a},
            'right_only': {k: b[k] for k in only_b}, 'mismatches': mismatches,
            'explained_no_coverage_refinements': refinements,
            'shared_classifications_agree': bool(shared) and not mismatches,
            'equal_mutation_scope': bool(shared) and not only_a and not only_b,
            'ratio_eligible': bool(shared) and not only_a and not only_b and not mismatches}


def prepare_project(destination, fixture):
    shutil.copytree(ROOT / 'fixtures' / fixture, destination,
                    ignore=shutil.ignore_patterns('node_modules', '.oximut', '.vitest', '.stryker-tmp*'))
    (destination / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
    # Valid JSON lockfile, inert snapshot and imported helper provide inputs.
    # These are not claims of snapshot execution or precise graph invalidation.
    (destination / 'package.json').write_text('{"type":"module","private":true}\n')
    (destination / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}\n')
    (destination / 'test/benchmark.snap').write_text('// inert benchmark fingerprint input v1\n')
    (destination / 'src/benchmark-helper.ts').write_text('export const version = 1;\n')
    first_test = next(iter(sorted((destination / 'test').glob('*.test.ts'))))
    first_test.write_text(first_test.read_text() +
                          "\nimport '../src/benchmark-helper.js';\n")
    template = (ROOT / 'benchmarks/config/m6-stryker.mjs').read_text()
    (destination / 'stryker.config.mjs').write_text(template)


def apply_change(project, fixture, change, env):
    if change == 'environment':
        env['OXIMUT_BENCH_INPUT'] = 'changed'
        return {'kind': change, 'description': 'declared environment fingerprint input; fixture behavior unchanged'}
    relative = {'source': 'src/' + FIXTURES[fixture][0], 'test': next(
                    p.relative_to(project).as_posix() for p in sorted((project / 'test').glob('*.test.ts'))),
                'helper': 'src/benchmark-helper.ts', 'snapshot': 'test/benchmark.snap',
                'config': 'vitest.config.ts', 'lockfile': 'package-lock.json'}[change]
    path = project / relative
    before = measurement.file_hash(path)
    if change == 'lockfile':
        value = json.loads(path.read_text())
        value['name'] = 'benchmark-input-change'
        write_json(path, value)
    elif change == 'helper':
        path.write_text('export const version = 2;\n')
    else:
        path.write_text(path.read_text() + '\n// M6 deterministic fingerprint edit\n')
    return {'kind': change, 'file': relative, 'before_sha256': before,
            'after_sha256': measurement.file_hash(path),
            'description': 'deterministic content edit; baseline behavior intentionally unchanged; '
                           'helper is imported by a test with unused export; snapshot is an inert fingerprint input'}


def command(args, tool, fixture, project, report, mode, cache=False, force=False, seed=100):
    if tool == 'vitest':
        return ['node', str(ROOT / 'node_modules/vitest/vitest.mjs'), 'run',
                '--config', 'vitest.config.ts', '--maxWorkers=1', '--no-file-parallelism',
                '--reporter=json', f'--outputFile={report}']
    if tool == 'stryker':
        return ['node', str(ROOT / 'node_modules/@stryker-mutator/core/bin/stryker.js'),
                'run', 'stryker.config.mjs']
    result = [str(args.binary), '--root', str(project), '--config', 'vitest.config.ts',
              '--report', str(report), '--workers', str(args.workers),
              '--order-seed', str(seed), '--execution', 'fresh' if mode == 'fresh' else 'persistent']
    for source in FIXTURES[fixture]:
        result.extend(['--source', 'src/' + source])
    if mode == 'selection':
        result.append('--test-selection')
    if cache:
        result.extend(['--cache', '--cache-inputs-complete', '--cache-env', 'OXIMUT_FIXTURE_MODE',
                       '--cache-env', 'OXIMUT_BENCH_INPUT'])
        if getattr(args, 'cache_policy', None) not in (None, 'legacy'):
            result.extend(['--cache-policy', args.cache_policy])
    else:
        result.append('--no-cache')
    if force:
        result.append('--force')
    if args.memory_budget_mib is not None:
        result.extend(['--memory-budget-mib', str(args.memory_budget_mib)])
    return result


def validate_oximut_cache(row, project, force, policy=None):
    cache = row.get('cache') or {}
    hits = cache.get('outcomeHits', 0)
    if force:
        if hits:
            raise ValueError('forced run reused outcome cache entries')
        return
    if (policy == 'auto' and row.get('role') == 'incremental'
            and cache.get('reason') == 'outcome-cache-cost-exceeds-fresh-work'):
        if (not row.get('cache_prime_validated') or cache.get('enabled') is not True
                or cache.get('outcomesEnabled') is not False or hits != 0
                or cache.get('fingerprintProfiles') != []
                or row.get('cache_provenance_count') != 0):
            raise ValueError('adaptive bypass lacks primed, entirely fresh execution evidence')
        row['cache_policy_bypass_verified'] = True
        # Fresh execution proves agreement, rather than a cache-invalidation policy.
        row['cache_invalidation_verified'] = None
        return
    if cache.get('outcomesEnabled') is not True:
        raise ValueError('outcome cache disabled; zero hits cannot prove invalidation')
    if row.get('role') == 'prime':
        fingerprint = cache.get('fingerprint')
        if not fingerprint or hits != 0 or cache.get('outcomeMisses') != row['mutant_count']:
            raise ValueError('cache prime lacks complete cold outcome misses/fingerprint')
        path = project / '.oximut/cache/outcomes' / (fingerprint + '.json')
        publication = measurement.read_json(path)
        if (publication.get('complete') is not True or publication.get('fingerprint') != fingerprint
                or publication.get('runId') != row['report_run_id']
                or len(publication.get('results', [])) != row['mutant_count']):
            raise ValueError('cache prime did not publish the complete current outcome bundle')
        row['cache_prime_publication_sha256'] = measurement.file_hash(path)
    elif row.get('role') == 'incremental':
        if not row.get('cache_prime_validated'):
            raise ValueError('incremental cache run lacks a successful persisted prime')
        changed = row.get('change', {}).get('kind') != 'unchanged'
        if changed and (hits != 0 or cache.get('outcomeMisses') != row['mutant_count']):
            raise ValueError('changed-input run did not invalidate every outcome')
        if not changed and hits != row['mutant_count']:
            raise ValueError('unchanged incremental run failed to reuse every outcome')
        row['cache_invalidation_verified'] = changed


def measured_run(args, tool, fixture, project, run_dir, mode, env, *, cache=False,
                 force=False, seed=100, **labels):
    run_dir.mkdir(parents=True)
    report = run_dir / 'report.json'
    # Report identity and inherited environment are cache inputs. Keep the live
    # destination stable across a cache pair, then archive each fresh report.
    execution_report = (project.parent / 'oximut-cache-report.json'
                        if tool == 'oximut' and cache else report)
    execution_report.unlink(missing_ok=True)
    process_env = {**env, 'OXIMUT_BENCH_REPORT': str(execution_report),
                   'OXIMUT_BENCH_INCREMENTAL': 'true' if cache and not force else 'false',
                   'OXIMUT_BENCH_INCREMENTAL_FILE': str(project.parent / 'stryker-incremental.json'),
                   'OXIMUT_BENCH_WORKERS': str(args.workers)}
    if tool == 'oximut' and cache:
        process_env['OXIMUT_BENCH_INCREMENTAL'] = 'true'
    cmd = command(args, tool, fixture, project, execution_report, mode, cache, force, seed)
    print(f'{run_dir.name}: {tool} {fixture} {labels.get("scenario")}', flush=True)
    row = measurement.run_command(cmd, project, run_dir / 'output.log', args.timeout,
                                  args.sample_interval, args.output_limit, process_env)
    if execution_report != report and execution_report.is_file():
        shutil.copy2(execution_report, report)
    if tool == 'oximut' and cache and getattr(args, 'cache_policy', None) == 'auto':
        # Preserve the advisory observations before the fixture is removed.
        # This happens outside the measured command and contains no raw inputs.
        history = {path.name: measurement.read_json(path)
                   for path in sorted((project / '.oximut/cache/policy').glob('*.json'))
                   if path.stat().st_size <= 8192}
        write_json(run_dir / 'policy-history.json', history)
    row.update(schema_version=SCHEMA_VERSION, tool=tool, fixture=fixture, mode=mode,
               report_file=str(report), seed=seed, cache_policy=getattr(args, 'cache_policy', None), **labels)
    config_inputs = {str(p.relative_to(project)): measurement.file_hash(p)
                     for p in project.rglob('*') if p.is_file()
                     and 'node_modules' not in p.relative_to(project).parts and '.oximut' not in p.relative_to(project).parts
                     and not any(part.startswith('.stryker-tmp') for part in p.relative_to(project).parts)}
    row['configuration_sha256'] = digest({'inputs': config_inputs, 'tool': tool, 'mode': mode,
                                          'cache': cache, 'force': force, 'workers': args.workers,
                                          'cache_policy': getattr(args, 'cache_policy', None),
                                          'memory_budget_mib': args.memory_budget_mib,
                                          'declared_environment': {k: env[k] for k in
                                              ('OXIMUT_FIXTURE_MODE', 'OXIMUT_BENCH_INPUT')}})
    try:
        row.update(artifact(tool, report, project, row))
        if tool == 'oximut' and cache:
            validate_oximut_cache(row, project, force, getattr(args, 'cache_policy', None))
        row['success'] = True  # Oximut exit 1 is a complete surviving-mutant result.
    except (OSError, ValueError, TypeError, KeyError) as error:
        row.update(success=False, validation_error=str(error))
    output_text = (run_dir / 'output.log').read_text(errors='replace')
    row['phase_metrics'] = measurement.parse_phase_metrics(output_text)
    if tool == 'stryker':
        plain = re.sub(r'\x1b\[[0-9;]*m', '', output_text)
        reused = re.search(r'(\d+) of (\d+) mutant result\(s\) are reused', plain)
        row['incremental_reused_mutants'] = int(reused[1]) if reused else (0 if not cache or force else None)
        row['incremental_reuse_source'] = 'explicit tool log; null means unavailable'
        if cache and not force and row['success']:
            try:
                if labels.get('role') == 'prime':
                    cached = project.parent / 'stryker-incremental.json'
                    artifact('stryker', cached, project, row)
                    row['cache_prime_publication_sha256'] = measurement.file_hash(cached)
                elif labels.get('role') == 'incremental':
                    if not labels.get('cache_prime_validated'):
                        raise ValueError('Stryker incremental run lacks a successful persisted prime')
                    if labels.get('change', {}).get('kind') == 'unchanged' and not row['incremental_reused_mutants']:
                        raise ValueError('Stryker unchanged incremental run has no explicit outcome reuse evidence')
                    row['cache_invalidation_verified'] = None
            except (OSError, ValueError, TypeError, KeyError) as error:
                row.update(success=False, validation_error=str(error))
    return row


def summarize(rows):
    groups = collections.defaultdict(list)
    for row in rows:
        if row['role'] != 'prime':
            groups[(row['fixture'], row['tool'], row['scenario'], row['role'])].append(row)
    result = []
    for key, group in sorted(groups.items()):
        measured = [r for r in group if not r['warmup']]
        successful = [r for r in measured if r['success']]
        stable = len({(r.get('mutation_identity_outcome_sha256'), r.get('test_identity_sha256'),
                       digest(r.get('baseline_test_counts'))) for r in group if r['success']}) <= 1
        item = dict(zip(('fixture', 'tool', 'scenario', 'role'), key))
        item.update(measured_attempts=len(measured), measured_successes=len(successful),
                    warmup_attempts=len(group) - len(measured), outcomes_stable=stable,
                    all_runs_successful=all(r['success'] for r in group),
                    mutation_status_counts=[r.get('mutation_status_counts') for r in measured])
        for metric in METRICS:
            values = [r[metric] for r in successful]
            item[metric] = {'samples': values, 'median': statistics.median(values),
                            'min': min(values), 'max': max(values)} if values else None
        result.append(item)
    return result


def gates_and_ratios(rows, summaries):
    comparisons, failures, ratios = [], [], []
    indexed = collections.defaultdict(list)
    for row in rows:
        if row['role'] != 'prime':
            indexed[(row['fixture'], row['round_index'])].append(row)
        if not row['success']:
            failures.append({'run': row['run_label'], 'error': row.get('validation_error', 'command failed')})
    for item in summaries:
        if not item['outcomes_stable']:
            failures.append({'group': [item[k] for k in ('fixture', 'tool', 'scenario', 'role')],
                             'error': 'outcomes/test identities changed across repetitions or warmup'})
    for (fixture, round_index), group in sorted(indexed.items()):
        reference = next((r for r in group if r['tool'] == 'oximut' and r['scenario'] == 'clean-fresh'), None)
        for row in group:
            left, kind = None, None
            if row['scenario'].startswith('clean-') and row is not reference and reference and row['tool'] != 'vitest':
                left, kind = reference, 'cross-tool' if row['tool'] == 'stryker' else 'optimized-reference'
            if row['role'] == 'incremental':
                left = next((r for r in group if r['tool'] == row['tool'] and r['scenario'] == row['scenario']
                             and r['role'] == 'forced'), None)
                kind = 'cache-forced'
            if left is None:
                continue
            comparison = compare(left, row, allow_uncovered=kind == 'optimized-reference')
            comparison.update(fixture=fixture, round_index=round_index, kind=kind,
                              left_run=left['run_label'], right_run=row['run_label'])
            comparisons.append(comparison)
            if (not left['success'] or not row['success']
                    or not comparison['shared_classifications_agree']
                    or (kind != 'cross-tool' and not comparison['equal_mutation_scope'])):
                failures.append({'left_run': left['run_label'], 'right_run': row['run_label'],
                                 'error': 'mutation identity/classification disagreement or failed pair'})
    # Ratios are gated globally. Unequal Stryker workloads remain raw descriptive
    # timings; matching a subset does not permit attributing full-run speedup.
    all_passed = not failures
    for fixture in sorted({r['fixture'] for r in rows}):
        for scenario, tool, kind in [('clean-persistent', 'oximut', 'optimized-reference'),
                                     ('clean-selection', 'oximut', 'optimized-reference'),
                                     ('clean-stryker', 'stryker', 'cross-tool')]:
            relevant = [c for c in comparisons if c['fixture'] == fixture and c['kind'] == kind
                        and any(r['run_label'] == c['right_run'] and r['scenario'] == scenario for r in rows)]
            a = [r for r in rows if r['fixture'] == fixture and r['scenario'] == 'clean-fresh' and not r['warmup']]
            b = [r for r in rows if r['fixture'] == fixture and r['scenario'] == scenario and not r['warmup']]
            if not a or not b:
                continue
            eligible = all_passed and bool(relevant) and all(c['ratio_eligible'] for c in relevant)
            value = {'fixture': fixture, 'numerator': 'oximut clean-fresh', 'denominator': f'{tool} {scenario}',
                     'eligible': eligible, 'numerator_samples': len(a), 'denominator_samples': len(b),
                     'reason': 'all gates pass and exact workloads agree' if eligible else
                               'correctness gate failed or exact full mutation workloads differ'}
            if eligible:
                value['wall_median_ratio'] = statistics.median(r['wall_seconds'] for r in a) / statistics.median(r['wall_seconds'] for r in b)
                value['peak_rss_median_ratio'] = statistics.median(r['peak_aggregate_rss_bytes'] for r in a) / statistics.median(r['peak_aggregate_rss_bytes'] for r in b)
            ratios.append(value)
    return {'passed': all_passed, 'failures': failures}, comparisons, ratios


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keep-projects', action='store_true', help='retain fixture workspaces and caches (default archives inputs and removes workspaces)')
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/debug/oximut')
    parser.add_argument('--fixtures', nargs='+', choices=FIXTURES, default=['pure', 'stateful'])
    parser.add_argument('--tools', nargs='+', choices=['vitest', 'oximut', 'stryker'], default=['vitest', 'oximut', 'stryker'])
    parser.add_argument('--scenarios', nargs='+', choices=['clean', 'incremental'], default=['clean', 'incremental'])
    parser.add_argument('--changes', nargs='*', choices=CHANGES, default=['source', 'test'])
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--cache-policy', choices=['auto', 'always', 'legacy'], default='always',
                        help='always measures actual reuse (default); auto measures adaptive decisions; legacy omits the flag for old binaries')
    parser.add_argument('--memory-budget-mib', type=int)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--sample-interval', type=float, default=.02)
    parser.add_argument('--output-limit', type=int, default=1_048_576)
    args = parser.parse_args(argv)
    if not measurement.LINUX_KERNEL:
        parser.error('Linux /proc and wait4 required')
    if (args.repetitions < 1 or args.warmups < 0 or args.workers < 1 or args.timeout <= 0
            or args.sample_interval <= 0 or args.output_limit < 1
            or (args.memory_budget_mib is not None and args.memory_budget_mib <= 64)):
        parser.error('positive limits required; memory budget must exceed default 64 MiB reserve')
    if args.scenarios == ['incremental'] and args.tools == ['vitest']:
        parser.error('ordinary Vitest requires the clean scenario')
    if 'oximut' in args.tools and not args.binary.is_file():
        parser.error('build Oximut first or supply --binary; build time is outside measurements')
    args.binary = args.binary.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    meta = measurement.metadata()
    meta['input_sha256'].update({str(p.relative_to(ROOT)): measurement.file_hash(p)
        for p in [Path(__file__), *sorted((ROOT / 'crates').rglob('*.rs')),
                  *sorted((ROOT / 'packages/vitest-adapter').glob('*.mjs')),
                  ROOT / 'packages/vitest-adapter/supervisor.py',
                  ROOT / 'scripts/validate-reports.mjs', ROOT / 'docs/report.schema.json']})
    meta.update(schema_version=SCHEMA_VERSION, created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                binary={'path': str(args.binary), 'sha256': measurement.file_hash(args.binary)
                        if args.binary.exists() else None, 'profile': 'user-supplied; default debug'},
                arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                policy={'repetitions': args.repetitions, 'warmups': args.warmups,
                    'ordering': 'fixture and scenario order reverse each round; incremental/forced order alternates',
                    'process_cold': True, 'filesystem_caches': 'untouched; warmups may warm OS caches',
                    'setup': 'installation/build excluded; fixture setup duration recorded; cache primes measured separately',
                    'cpu_allocation': 'inherited affinity/cgroup recorded; equal configured outer workers; Vitest maxWorkers=1',
                    'memory': 'sampled aggregate process-tree RSS via run.py; shared pages double counted; peaks/short jobs may be missed; harness excluded',
                    'cpu': 'wait4 process-tree rusage via run.py; includes reaped descendants; un-reaped descendants may be missing',
                    'memory_budget': 'Oximut sampled soft admission only; Stryker has no matching application cap',
                    'typescript_checkers': False, 'stryker_coverage': 'perTest; worker reuse default; incremental for cache pairs',
                    'identity': 'exact edited source plus relative filename; whitespace/parentheses differences excluded',
                    'cross_tool_ratio': 'suppressed unless exact full measured mutation sets and classifications agree; explicitly configured ignored operators published separately as scope exclusions',
                    'scope': 'checked-in deterministic fixtures only; no real projects, generated scaling, memory stress, cancellation or alpha release decision',
                    'changed_inputs': 'deterministic behavior-preserving edits; helper imported by a test (unused export), snapshot inert',
                    'cache_evidence': 'Oximut always/legacy requires enabled outcome cache, persisted prime and changed-input zero hits; explicit auto cost bypass requires primed entirely fresh execution and is not invalidation evidence; Stryker unchanged requires explicit reuse, changed-input agreement does not prove invalidation under its narrower reuse policy',
                    'declared_environment': {'CI': 'true', 'NO_COLOR': '1', 'OXIMUT_FIXTURE_MODE': 'fixture', 'OXIMUT_BENCH_INPUT': 'original'}})
    write_json(output / 'metadata.json', meta)
    rows = []
    with (output / 'measurements.jsonl').open('w') as stream:
        def record(row):
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush()
        for round_index in range(args.warmups + args.repetitions):
            warmup = round_index < args.warmups
            repetition = round_index + 1 if warmup else round_index - args.warmups + 1
            fixtures = args.fixtures if round_index % 2 == 0 else list(reversed(args.fixtures))
            for fixture in fixtures:
                tasks = []
                if 'clean' in args.scenarios:
                    if 'vitest' in args.tools:
                        tasks.append(('vitest', 'ordinary', 'fresh'))
                    if 'oximut' in args.tools:
                        tasks.extend(('oximut', 'clean-' + mode, mode) for mode in ('fresh', 'persistent', 'selection'))
                    if 'stryker' in args.tools:
                        tasks.append(('stryker', 'clean-stryker', 'selection'))
                if 'incremental' in args.scenarios:
                    tasks.extend((tool, 'incremental-' + change, 'selection')
                                 for change in ['unchanged', *args.changes]
                                 for tool in args.tools if tool != 'vitest')
                if round_index % 2:
                    tasks.reverse()
                for tool, scenario, mode in tasks:
                    label = f'{"warmup" if warmup else "measured"}-{repetition:02d}-{fixture}-{tool}-{scenario}'
                    directory = output / label
                    directory.mkdir()
                    project = directory / 'project'
                    setup_started = time.monotonic()
                    prepare_project(project, fixture)
                    setup_seconds = time.monotonic() - setup_started
                    env = {**os.environ, 'CI': 'true', 'NO_COLOR': '1',
                           'OXIMUT_FIXTURE_MODE': 'fixture', 'OXIMUT_BENCH_INPUT': 'original'}
                    labels = dict(scenario=scenario, warmup=warmup, repetition=repetition,
                                  round_index=round_index, setup_seconds=setup_seconds)
                    if scenario.startswith('incremental-'):
                        prime = measured_run(args, tool, fixture, project, directory / 'prime', mode,
                            env, cache=True, role='prime', run_label=label + '-prime', **labels)
                        record(prime)
                        change = scenario.removeprefix('incremental-')
                        change_info = {'kind': 'unchanged'} if change == 'unchanged' else apply_change(project, fixture, change, env)
                        roles = ['incremental', 'forced'] if round_index % 2 == 0 else ['forced', 'incremental']
                        for role in roles:
                            record(measured_run(args, tool, fixture, project, directory / role, mode,
                                env, cache=True, force=role == 'forced', role=role, change=change_info,
                                run_label=label + '-' + role, cache_prime_validated=prime['success'], **labels))
                    else:
                        record(measured_run(args, tool, fixture, project, directory / 'run', mode,
                            env, role='ordinary' if tool == 'vitest' else 'clean',
                            run_label=label, **labels))
                    write_json(directory / 'project-inputs.json', {
                        str(p.relative_to(project)): p.read_text() for p in project.rglob('*')
                        if p.is_file() and 'node_modules' not in p.relative_to(project).parts and '.oximut' not in p.relative_to(project).parts
                        and not any(part.startswith('.stryker-tmp') for part in p.relative_to(project).parts)})
                    if not args.keep_projects:
                        shutil.rmtree(project)
                        (directory / 'stryker-incremental.json').unlink(missing_ok=True)
    summary = summarize(rows)
    gate, comparisons, ratios = gates_and_ratios(rows, summary)
    write_json(output / 'summary.json', {'groups': summary, 'correctness_gate': gate,
                                        'ratios': ratios, 'raw_measurement_count': len(rows)})
    write_json(output / 'comparisons.json', comparisons)
    with (output / 'measurements.csv').open('w', newline='') as stream:
        columns = ['run_label', 'fixture', 'tool', 'scenario', 'role', 'repetition', 'warmup',
                   'success', 'configuration_sha256', *METRICS, 'mutant_count', 'report_file']
        writer = csv.DictWriter(stream, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(f'Correctness gate: {"passed" if gate["passed"] else "FAILED"}; {output / "summary.json"}', flush=True)
    return 0 if gate['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
