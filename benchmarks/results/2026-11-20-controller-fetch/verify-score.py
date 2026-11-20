"""Recompute unprofiled paired scores and bind every round to historical identities."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from compare_candidates import evaluate, phase_errors, plan
from profile_native import native_coordinator_evidence
from real_project import load_manifest


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def relative(path, root):
    return Path(path).relative_to(root).as_posix()


def exact_mutants(row):
    normalized = {}
    for key, mutant in row['mutations'].items():
        mutant = dict(mutant)
        if row['tool'] == 'oximut':
            selection = mutant['selection']
            mutant['selection'] = {**selection, 'files': sorted(
                relative(file, row['cwd']) for file in selection['files'])}
        else:
            # Stryker's ordinal IDs can differ with discovery order.
            mutant.pop('id')
        normalized[key] = mutant
    return normalized


def baseline_tests(report, row):
    coverage = report['baseline']['coverage']
    identities = {(relative(test['file'], row['cwd']), test['name']) for test in coverage['tests']}
    require(coverage['complete'] and len(coverage['tests']) == len(identities) == 102,
            'incomplete baseline test attribution')
    return identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('score', type=Path)
    parser.add_argument('historical_capture', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    historical = args.historical_capture / 'qualification'
    reference = {row['tool']: row for row in rows(historical / 'measurements.jsonl')}
    metadata = read_json(args.score / 'metadata.json')
    stored = read_json(args.score / 'summary.json')
    evidence = rows(args.score / 'measurements.jsonl')
    attempts = read_json(args.score / 'attempts.json')
    require((metadata['warmups'], metadata['repetitions'], metadata['concurrency'], metadata['workload'])
            == (1, 6, 4, 'colord-scale'), 'wrong campaign dimensions')
    require(len(attempts) == 14 and len(evidence) == 42, 'incomplete campaign')
    for attempt, (index, warmup, variant, tools) in zip(attempts, plan(1, 6), strict=True):
        command = attempt['command']
        tool_index = command.index('--tools') + 1
        require((attempt['round'], attempt['warmup'], attempt['variant']) == (index, warmup, variant)
                and command[tool_index:tool_index + 3] == tools, 'wrong variant or tool order')
        require(attempt.get('exit_code') == 0 and not attempt.get('descendant_cleanup_required'),
                'failed invocation or required descendant cleanup')
    result = evaluate(evidence, 1, 6, metadata['runtime_provenance'],
                      concurrency=4, workload_name='colord-scale')
    require(result['correctness_gate']['passed'], result['correctness_gate']['errors'])
    require(all(stored[key] == value for key, value in result.items()), 'reported score differs from replay')

    manifest = load_manifest('colord-scale')
    historical_tests = baseline_tests(read_json(historical / '00-oximut/report.json'), reference['oximut'])
    for row in evidence:
        tool = row['tool']
        old = reference[tool]
        require(all(row.get(key) == old.get(key) for key in
                    ('workload_sha256', 'test_identity_sha256', 'declared_test_identity_sha256',
                     'mutation_identity_outcome_sha256', 'test_counts', 'baseline_test_counts')),
                f'{tool} historical workload identity mismatch')
        require(row['success'] and not row['descendant_cleanup_required'], f'{tool} run did not exit cleanly')
        if tool != 'vitest':
            require(exact_mutants(row) == exact_mutants(old), f'{tool} historical mutant mismatch')
        if tool == 'oximut':
            directory = args.score / Path(row['subcampaign']).name
            report = read_json(directory / relative(row['report_file'], row['subcampaign']))
            require(baseline_tests(report, row) == historical_tests, 'baseline tests changed')
            require(any('isolation=fresh-test-forks' in line for line in report['diagnostics']),
                    'fresh fork isolation missing from report')
            require(native_coordinator_evidence(directory) and not phase_errors(row, manifest),
                    'native coordinator phase evidence failed')
    record = {
        'verified_rounds': 7, 'verified_measurements': len(evidence),
        'historical_outcomes': dict(Counter(m['outcome'] for m in reference['oximut']['mutations'].values())),
        'historical_tests': len(historical_tests),
        'comparisons': result['comparisons'], 'targets_met': result['targets_met'],
    }
    args.output.write_text(json.dumps(record, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
