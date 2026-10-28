#!/usr/bin/env python3
"""Evaluate predeclared matched-workload performance targets from M6 evidence."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import random
import statistics

import m6

PRIMARY_MODE = 'clean-selection'
FIXTURES = ('matched-small', 'matched-scale')
THRESHOLDS = {'wall_seconds': .80, 'peak_aggregate_rss_bytes': .50}


def paired_interval(pairs, seed=20261027):
    """Deterministic percentile bootstrap of paired median ratios (10,000 draws)."""
    rng = random.Random(seed)
    ratios = [a / b for a, b in pairs]
    draws = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(10000))
    return {'median': statistics.median(ratios), 'min': min(ratios), 'max': max(ratios),
            'bootstrap_95_percent': [draws[249], draws[9749]], 'paired_ratios': ratios}


def evaluate(rows):
    results = []
    for fixture in FIXTURES:
        relevant = [r for r in rows if r['fixture'] == fixture]
        indexed = {(r['round_index'], r['scenario']): r for r in relevant}
        rounds = sorted({r['round_index'] for r in relevant})
        errors, pairs = [], {metric: [] for metric in (*THRESHOLDS, 'cpu_seconds')}
        if len(indexed) != len(relevant):
            errors.append('duplicate round/scenario evidence')
        warmups = 0
        for index in rounds:
            a, b = (indexed.get((index, scenario)) for scenario in (PRIMARY_MODE, 'clean-stryker'))
            if a is None or b is None:
                errors.append(f'round {index}: missing primary pair')
                continue
            comparison = m6.compare(a, b)
            fresh = indexed.get((index, 'clean-fresh'))
            if (not fresh or not fresh['success']
                    or not m6.compare(fresh, a)['ratio_eligible']
                    or not m6.compare(fresh, b)['ratio_eligible']
                    or fresh.get('matched_manifest_sha256') != a.get('matched_manifest_sha256')
                    or fresh['warmup'] != a['warmup']):
                errors.append(f'round {index}: missing, failed or unequal fresh correctness reference')
            ordinary = indexed.get((index, 'ordinary'))
            if (not ordinary or not ordinary['success']
                    or ordinary.get('matched_test_identity_sha256') != a.get('matched_test_identity_sha256')
                    or ordinary.get('matched_manifest_sha256') != a.get('matched_manifest_sha256')):
                errors.append(f'round {index}: missing or unequal ordinary baseline inventory')
            if not a['success'] or not b['success'] or not comparison['ratio_eligible']:
                errors.append(f'round {index}: failed run or unequal exact mutation workload/outcomes')
            if (not a.get('matched_test_identity_sha256')
                    or a.get('matched_test_identity_sha256') != b.get('matched_test_identity_sha256')
                    or not a.get('matched_manifest_sha256')
                    or a.get('matched_manifest_sha256') != b.get('matched_manifest_sha256')):
                errors.append(f'round {index}: missing or unequal declared test inventory/manifest')
            if any(r.get('memory_method') != 'sum-live-smaps_rollup-rss' or r.get('memory_errors') for r in (a, b)):
                errors.append(f'round {index}: incomplete accurate memory accounting')
            if a['warmup'] != b['warmup']:
                errors.append(f'round {index}: inconsistent warmup labels')
            if a['warmup']:
                warmups += 1
                continue
            for metric in pairs:
                numerator, denominator = a.get(metric), b.get(metric)
                valid = all(type(v) in (int, float) and math.isfinite(v)
                            for v in (numerator, denominator))
                if (not valid or denominator <= 0 or numerator < 0
                        or (metric != 'cpu_seconds' and numerator == 0)):
                    errors.append(f'round {index}: unavailable {metric}')
                else:
                    pairs[metric].append((a[metric], b[metric]))
        if not rounds:
            errors.append('missing fixture evidence')
        sufficient = warmups >= 1 and all(len(p) >= 5 for p in pairs.values())
        item = {'fixture': fixture, 'numerator': 'Oximut persistent controller with file selection',
                'denominator': 'Stryker perTest selection', 'errors': errors,
                'status': 'invalid workload' if errors else 'inconclusive',
                'warmup_pairs': warmups, 'measured_pairs': len(pairs['wall_seconds']), 'metrics': {}}
        if not errors:
            for metric, values in pairs.items():
                if not values:
                    continue
                result = paired_interval(values)
                if metric in THRESHOLDS:
                    threshold = THRESHOLDS[metric]
                    lo, hi = result['bootstrap_95_percent']
                    result.update(target_ratio=threshold, median_target_met=result['median'] <= threshold,
                                  status=('inconclusive' if not sufficient else 'target met' if hi <= threshold
                                          else 'target missed' if lo > threshold else 'inconclusive'))
                else:
                    result['median_cpu_reduction_percent'] = 100 * (1 - result['median'])
                item['metrics'][metric] = result
            statuses = [v['status'] for k, v in item['metrics'].items() if k in THRESHOLDS]
            if sufficient and len(statuses) == 2:
                item['status'] = ('target met' if all(s == 'target met' for s in statuses)
                                  else 'target missed' if 'target missed' in statuses else 'inconclusive')
        results.append(item)
    return {'primary_mode': PRIMARY_MODE, 'all_scales_required': True,
            'thresholds': THRESHOLDS, 'minimum_measured_pairs': 5, 'minimum_warmup_pairs': 1,
            'confidence': 'deterministic paired percentile bootstrap; exploratory, host/corpus-specific',
            'targets_met': all(r['status'] == 'target met' for r in results), 'fixtures': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--require-targets', action='store_true')
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in (args.campaign / 'measurements.jsonl').read_text().splitlines()]
    summary = json.loads((args.campaign / 'summary.json').read_text())
    result = evaluate(rows)
    result['correctness_gate'] = summary['correctness_gate']
    if not summary['correctness_gate']['passed']:
        result['targets_met'] = False
    m6.write_json(args.campaign / 'performance-targets.json', result)
    print(json.dumps(result, indent=2))
    invalid = not summary['correctness_gate']['passed'] or any(r['errors'] for r in result['fixtures'])
    return 1 if invalid or (args.require_targets and not result['targets_met']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
