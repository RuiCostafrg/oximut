"""Performance claims require complete, equivalent, repeated evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import targets


def evidence(wall=.7, memory=.4, cpu=.6, repetitions=5):
    rows = []
    for fixture in targets.FIXTURES:
        for index in range(repetitions + 1):
            for scenario in ('ordinary', 'clean-selection', 'clean-stryker'):
                is_oximut = scenario == 'clean-selection'
                rows.append(dict(fixture=fixture, round_index=index, scenario=scenario,
                    success=True, warmup=index == 0, memory_method='sum-live-smaps_rollup-rss',
                    memory_errors=[], matched_test_identity_sha256='identical-tests',
                    matched_manifest_sha256='identical-manifest',
                    mutations={'edit': {'outcome': 'killed'}}, wall_seconds=wall if is_oximut else 1.,
                    peak_aggregate_rss_bytes=memory * 1000 if is_oximut else 1000,
                    cpu_seconds=cpu if is_oximut else 1.))
    return rows


class TargetTests(unittest.TestCase):
    def test_impossible_nonfinite_and_nonnumeric_measurements_cannot_certify(self):
        for value in (0, -1, float('inf'), float('nan'), None, '1', True):
            for metric in ('wall_seconds', 'peak_aggregate_rss_bytes'):
                for index in (4, 5):
                    rows = evidence()
                    rows[index][metric] = value
                    with self.subTest(value=value, metric=metric, index=index):
                        self.assertFalse(targets.evaluate(rows)['targets_met'])

    def test_both_targets_at_every_scale_and_cpu_are_reported(self):
        result = targets.evaluate(evidence())
        self.assertTrue(result['targets_met'])
        self.assertAlmostEqual(result['fixtures'][0]['metrics']['cpu_seconds']['median_cpu_reduction_percent'], 40)
        result = targets.evaluate(evidence(memory=.7))
        self.assertFalse(result['targets_met'])
        self.assertEqual(result['fixtures'][0]['status'], 'target missed')
        self.assertEqual(result['fixtures'][0]['metrics']['wall_seconds']['status'], 'target met')

    def test_smoke_missing_baseline_or_inaccurate_memory_cannot_certify(self):
        self.assertFalse(targets.evaluate(evidence(repetitions=1))['targets_met'])
        for field, value in [('memory_method', 'stat-rss'), ('memory_errors', ['restricted']),
                             ('matched_test_identity_sha256', 'different')]:
            rows = evidence()
            rows[1][field] = value
            self.assertEqual(targets.evaluate(rows)['fixtures'][0]['status'], 'invalid workload')
        rows = [r for r in evidence() if r['scenario'] != 'ordinary']
        self.assertFalse(targets.evaluate(rows)['targets_met'])

    def test_equal_counts_with_different_mutations_or_outcomes_are_invalid(self):
        for mutations in ({'another-edit': {'outcome': 'killed'}}, {'edit': {'outcome': 'survived'}}):
            rows = evidence()
            rows[1]['mutations'] = mutations
            self.assertEqual(targets.evaluate(rows)['fixtures'][0]['status'], 'invalid workload')

    def test_duplicate_rows_missing_scale_or_pair_are_invalid(self):
        rows = evidence()
        rows.append(rows[1])
        self.assertFalse(targets.evaluate(rows)['targets_met'])
        self.assertFalse(targets.evaluate([r for r in evidence() if r['fixture'] == 'matched-small'])['targets_met'])
        rows = evidence()
        del rows[1]
        self.assertFalse(targets.evaluate(rows)['targets_met'])

    def test_interval_is_deterministic_and_uncertainty_is_not_a_win(self):
        pairs = [(x, 1.) for x in (.5, .6, .7, .95, 1.1)]
        self.assertEqual(targets.paired_interval(pairs), targets.paired_interval(pairs))
        rows = evidence()
        for r in rows:
            if r['scenario'] == 'clean-selection' and not r['warmup']:
                r['wall_seconds'] = pairs[r['round_index'] - 1][0]
        self.assertEqual(targets.evaluate(rows)['fixtures'][0]['metrics']['wall_seconds']['status'], 'inconclusive')
        self.assertFalse(targets.evaluate(rows)['targets_met'])


if __name__ == '__main__':
    unittest.main()
