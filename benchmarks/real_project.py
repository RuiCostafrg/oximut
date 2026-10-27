#!/usr/bin/env python3
"""Pinned upstream corpus, sequential Linux process-tree measurements and exact gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics

import m6
import run as measurement

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'benchmarks/real-project/colord'
METRICS = m6.METRICS


def verify_inputs(project, manifest):
    actual = {p.relative_to(project).as_posix() for folder in ('src', 'tests')
              for p in (project / folder).rglob('*.ts')}
    if actual != set(manifest['upstreamSha256']):
        raise ValueError('upstream source/test file inventory changed')
    for name, expected in {**manifest['upstreamSha256'],
                           **manifest.get('upstreamMetadataSha256', {})}.items():
        if measurement.file_hash(project / name) != expected:
            raise ValueError(f'upstream source/test hash changed: {name}')
    if not set(manifest['mutate']) <= actual:
        raise ValueError('mutation source absent from upstream inventory')


def relative(filename, project):
    path = Path(filename)
    return path.relative_to(project).as_posix() if path.is_absolute() else path.as_posix()


def validate_tests(tool, report, project, manifest):
    if tool == 'vitest':
        identities = [[relative(suite['name'], project), test['fullName']]
                      for suite in report['testResults'] for test in suite['assertionResults']]
    elif tool == 'stryker':
        identities = [[relative(filename, project), test['name']]
                      for filename, suite in report['testFiles'].items() for test in suite['tests']]
    else:
        baseline = report['baseline']
        if baseline['tests']['passed'] != len(manifest['expectedTests']):
            raise ValueError('Oximut baseline count differs from declared upstream tests')
        coverage = baseline.get('coverage') or {}
        if not coverage.get('complete') or coverage.get('reasons'):
            raise ValueError('Oximut baseline test attribution incomplete')
        identities = [[relative(test['file'], project), test['name']]
                      for test in coverage['tests']]
    expected = manifest['expectedOximutTests'] if tool == 'oximut' else manifest['expectedTests']
    if sorted(identities) != sorted(expected):
        raise ValueError(f'{tool} test identities differ from declared upstream tests')
    # Both exact representations are pinned from ordinary Vitest ancestorTitles/title.
    # No fuzzy rewriting of emitted names is permitted.
    return m6.digest(sorted(manifest['expectedTests']))


def validate(tool, report_path, project, row, manifest):
    verify_inputs(project, manifest)
    artifact = m6.artifact(tool, report_path, project, row)
    row.update(artifact)  # Keep parsed scope evidence even when later test gates reject the run.
    report = measurement.read_json(report_path)
    artifact['declared_test_identity_sha256'] = validate_tests(tool, report, project, manifest)
    if tool != 'vitest':
        if any(m['file'] not in manifest['mutate'] for m in artifact['mutations'].values()):
            raise ValueError('mutant outside declared source scope')
        if not artifact['known_killed_sentinel_identity']:
            # Exact production edit, directly asserted by the upstream invert tests.
            filename = 'src/manipulate/invert.ts'
            source = (project / filename).read_text()
            sentinel = filename + ':' + hashlib.sha256(
                source.replace('255 - rgba.r', '255 + rgba.r', 1).encode()).hexdigest()
            if artifact['mutations'].get(sentinel, {}).get('outcome') != 'killed':
                raise ValueError('upstream invert arithmetic sentinel was not killed')
            artifact['known_killed_sentinel_identity'] = sentinel
    return artifact


def prepare(project, manifest):
    shutil.copytree(CORPUS, project, ignore=shutil.ignore_patterns(
        'node_modules', '.oximut', '.vitest', '.stryker-tmp*'))
    (project / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
    verify_inputs(project, manifest)
    config = {
        'mutate': manifest['mutate'],
        'files': ['src/**/*.ts', 'tests/**/*.ts', 'vitest.config.ts', 'package.json'],
        'testRunner': 'vitest', 'vitest': {'configFile': 'vitest.config.ts', 'related': False},
        'coverageAnalysis': 'perTest', 'concurrency': 1, 'checkers': [],
        'mutator': {'excludedMutations': [
            'EqualityOperator', 'AssignmentOperator', 'CallExpression', 'ArrayDeclaration',
            'ArrowFunction', 'BlockStatement', 'ConditionalExpression', 'LogicalOperator',
            'MethodExpression', 'ObjectLiteral', 'OptionalChaining', 'Regex', 'StringLiteral',
            'UnaryOperator', 'UpdateOperator',
        ]},
        'incremental': False, 'reporters': ['json', 'progress'],
        'tempDirName': '.stryker-tmp-real-project', 'cleanTempDir': True,
    }
    (project / 'stryker.config.mjs').write_text(
        'export default { ...' + json.dumps(config) +
        ', jsonReporter: { fileName: process.env.OXIMUT_BENCH_REPORT } };\n')


def command(tool, args, project, report, manifest):
    if tool == 'vitest':
        return ['node', str(ROOT / 'node_modules/vitest/vitest.mjs'), 'run',
                '--config', 'vitest.config.ts', '--reporter=json', f'--outputFile={report}']
    if tool == 'stryker':
        return ['node', str(ROOT / 'node_modules/@stryker-mutator/core/bin/stryker.js'),
                'run', 'stryker.config.mjs']
    result = [str(args.binary), '--root', str(project), '--config', 'vitest.config.ts',
              '--report', str(report), '--workers', '1', '--execution', 'persistent',
              '--test-selection', '--no-cache', '--order-seed', '100']
    if args.adapter is not None:
        result.extend(['--adapter', str(args.adapter)])
    for filename in manifest['mutate']:
        result.extend(['--source', filename])
    if not args.diagnostic_unrestricted:
        result.extend(['--mutators', ','.join(manifest['families'])])
    return result



def execution_inputs(args):
    """Hash actual selected adapter sources; callers assert default-binary pairing."""
    sources = {}
    if args.adapter_source_dir is not None:
        for path in sorted(args.adapter_source_dir.rglob('*')):
            if path.is_file() and not any(part in {'node_modules', '__pycache__', '.git'}
                                          for part in path.relative_to(args.adapter_source_dir).parts):
                sources[path.relative_to(args.adapter_source_dir).as_posix()] = measurement.file_hash(path)
    return {
        'binary': str(args.binary),
        'binary_sha256': measurement.file_hash(args.binary) if args.binary.is_file() else None,
        'adapter_override': str(args.adapter) if args.adapter is not None else None,
        'adapter_source_dir': str(args.adapter_source_dir) if args.adapter_source_dir is not None else None,
        'adapter_source_sha256': sources,
        'adapter_pairing': 'explicit override' if args.adapter is not None else
                           'binary default; caller asserts source directory matches compiled candidate',
    }


def compare_rows(rows):
    comparisons, ratios = [], []
    for index in sorted({row['round'] for row in rows}):
        group = {row['tool']: row for row in rows if row['round'] == index}
        if not {'vitest', 'oximut', 'stryker'} <= group.keys():
            continue
        ordinary, left, right = (group[k] for k in ('vitest', 'oximut', 'stryker'))
        comparison = m6.compare(left, right)
        comparison['round'] = index
        comparison['declared_families_agree'] = left.get('mutator_families') == right.get('mutator_families')
        comparison['ratio_eligible'] &= comparison['declared_families_agree']
        comparison['ratio_eligible'] &= all(row['success'] for row in group.values())
        comparison['ratio_eligible'] &= len({row.get('declared_test_identity_sha256')
                                             for row in group.values()}) == 1
        comparisons.append(comparison)
        if comparison['ratio_eligible'] and not ordinary['warmup']:
            ratios.append({'round': index, 'direction': 'stryker/oximut',
                           **{metric: right[metric] / left[metric] for metric in METRICS
                              if left[metric] > 0}})
    return comparisons, ratios


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/release/oximut')
    parser.add_argument('--adapter', type=Path,
                        help='Explicit adapter entrypoint; omitted uses the binary matching default')
    parser.add_argument('--adapter-source-dir', type=Path,
                        help='Full matching adapter package directory to hash; defaults to override parent')
    parser.add_argument('--tools', choices=['vitest', 'oximut', 'stryker'], nargs='+',
                        default=['vitest', 'oximut', 'stryker'])
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--repetitions', type=int, default=5)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--diagnostic-unrestricted', action='store_true',
                        help='Oximut full operator set; mismatches remain ineligible for ratios')
    args = parser.parse_args(argv)
    if args.warmups < 0 or args.repetitions < 1:
        parser.error('require warmups >= 0 and repetitions >= 1')
    args.binary = args.binary.resolve()
    if args.adapter is not None:
        args.adapter = args.adapter.resolve()
        if not args.adapter.is_file():
            parser.error('--adapter must name an existing entrypoint')
        if args.adapter_source_dir is None:
            args.adapter_source_dir = args.adapter.parent
    if args.adapter_source_dir is not None:
        args.adapter_source_dir = args.adapter_source_dir.resolve()
        if not (args.adapter_source_dir / 'm2-runner.mjs').is_file():
            parser.error('--adapter-source-dir must contain the matching m2-runner.mjs')
        if args.adapter is not None and not args.adapter.is_relative_to(args.adapter_source_dir):
            parser.error('--adapter must be inside --adapter-source-dir')
    if 'oximut' in args.tools and args.adapter_source_dir is None:
        parser.error('provide --adapter or --adapter-source-dir to record matching adapter provenance')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / '.gitignore').write_text('*/project/\n')
    manifest = measurement.read_json(CORPUS / 'workload.json')
    verify_inputs(CORPUS, manifest)
    meta = measurement.metadata()
    if (meta['versions']['node'] != 'v26.3.1' or meta['versions']['vitest'] != '4.1.12'
            or meta['versions']['@stryker-mutator/core'] != '10.0.0'
            or meta['versions']['@stryker-mutator/vitest-runner'] != '10.0.0'):
        raise ValueError('pinned Node/Vitest/Stryker toolchain required')
    meta.update(execution_inputs=execution_inputs(args), workload=manifest, diagnostic_unrestricted=args.diagnostic_unrestricted,
                binary_sha256=measurement.file_hash(args.binary) if args.binary.exists() else None,
                harness_sha256={str(path.relative_to(ROOT)): measurement.file_hash(path)
                                for path in [Path(__file__), Path(m6.__file__), Path(measurement.__file__),
                                             CORPUS / 'vitest.config.ts', CORPUS / 'package.json']},
                policy='Clean private copies; no cache; sequential tools, alternating order. '
                       'RSS is sampled aggregate smaps_rollup, CPU is process-tree wait4 rusage. '
                       'All raw artifacts/projects retained. Ratios require all tests and exact full '
                       'edited-source identities/outcomes; no overlap normalization. '
                       'Clean performance only; this harness makes no cache validation claim.')
    m6.write_json(output / 'metadata.json', meta)
    rows = []
    with (output / 'measurements.jsonl').open('w') as stream:
        for index in range(args.warmups + args.repetitions):
            tools = args.tools if index % 2 == 0 else list(reversed(args.tools))
            for tool in tools:
                directory = output / f'{index:02d}-{tool}'
                directory.mkdir()
                project, report = directory / 'project', directory / 'report.json'
                prepare(project, manifest)
                m6.write_json(directory / 'project-inputs.json', {
                    'sha256': {str(path.relative_to(project)): measurement.file_hash(path)
                               for path in project.rglob('*') if path.is_file()
                               and 'node_modules' not in path.relative_to(project).parts},
                    'harnessConfig': {name: (project / name).read_text() for name in
                                      ('vitest.config.ts', 'stryker.config.mjs', 'package.json')},
                    'sourceContent': 'Unmodified vendored files identified by workload upstreamSha256; '
                                     'full private project retained locally and in CI artifacts.',
                })
                env = {**os.environ, 'CI': 'true', 'NO_COLOR': '1',
                       'OXIMUT_BENCH_REPORT': str(report)}
                cmd = command(tool, args, project, report, manifest)
                inputs = execution_inputs(args)
                print(f'round {index}: {tool}', flush=True)
                row = measurement.run_command(cmd, project, directory / 'output.log',
                                              args.timeout, .02, 16_777_216, env)
                row.update(execution_inputs=inputs, tool=tool, round=index, warmup=index < args.warmups,
                           report_file=str(report), workload_sha256=m6.digest(manifest),
                           mutator_families=(['unrestricted'] if tool == 'oximut'
                                             and args.diagnostic_unrestricted else manifest['families']))
                try:
                    if execution_inputs(args) != inputs:
                        raise ValueError('binary or adapter sources changed during measured command')
                    row.update(validate(tool, report, project, row, manifest))
                    row['success'] = True
                except (OSError, ValueError, TypeError, KeyError) as error:
                    row.update(success=False, validation_error=str(error))
                rows.append(row)
                stream.write(json.dumps(row, sort_keys=True) + '\n')
                stream.flush()
                print(f"  success={row['success']} wall={row['wall_seconds']:.3f}s "
                      f"cpu={row['cpu_seconds']:.3f}s rss={row['peak_aggregate_rss_bytes']}", flush=True)
    comparisons, ratios = compare_rows(rows)
    passed = (all(row['success'] for row in rows) and bool(comparisons)
              and all(item['ratio_eligible'] for item in comparisons))
    m6.write_json(output / 'comparisons.json', comparisons)
    groups = []
    for tool in args.tools:
        measured = [row for row in rows if row['tool'] == tool and not row['warmup']]
        groups.append({'tool': tool, 'runs': len(measured),
                       'successes': sum(row['success'] for row in measured),
                       'raw_medians': {metric: statistics.median(row[metric] for row in measured)
                                       for metric in METRICS}})
    m6.write_json(output / 'summary.json', {'correctness_gate': passed, 'groups': groups,
                                          'ratios': ratios, 'raw_measurement_count': len(rows)})
    print(f'Exact comparison gate: {passed}; {output}', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
