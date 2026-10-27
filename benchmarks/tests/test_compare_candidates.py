"""Candidate campaigns reject partial, unstable or cross-candidate unequal evidence."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compare_candidates as campaign


class CampaignTests(unittest.TestCase):
    def evidence(self):
        manifest = json.loads((campaign.workload.CORPUS / 'workload.json').read_text())
        provenance = {variant: {'binary_sha256': variant, 'adapter_source_sha256': {'runner': variant}}
                      for variant in campaign.VARIANTS}
        rows = []
        for index in range(6):
            for variant in campaign.VARIANTS:
                for tool in ('vitest', 'oximut', 'stryker'):
                    row = dict(campaign_round=index, round=0, variant=variant, tool=tool,
                               warmup=index == 0, success=True,
                               memory_method='sum-live-smaps_rollup-rss', memory_errors=[],
                               declared_test_identity_sha256=campaign.m6.digest(sorted(manifest['expectedTests'])),
                               workload_sha256=campaign.m6.digest(manifest),
                               mutator_families=manifest['families'],
                               execution_inputs=copy.deepcopy(provenance[variant]),
                               mutations={} if tool == 'vitest' else {'exact': {'outcome': 'killed'}},
                               wall_seconds=10., cpu_seconds=10., peak_aggregate_rss_bytes=100.,
                               peak_process_count=3)
                    if tool == 'oximut':
                        row.update(wall_seconds=5. if variant == 'before' else 2.,
                                   cpu_seconds=4., peak_aggregate_rss_bytes=20.)
                    rows.append(row)
        return rows, provenance

    def test_paired_ratios_and_cpu_without_invented_cpu_target(self):
        rows, provenance = self.evidence()
        rows[-2]['resources'] = {'phaseWallMs': {'baseline': 123.}, 'disabledValidationRuns': 1}
        result = campaign.evaluate(rows, 1, 5, provenance)
        self.assertEqual(result['phase_evidence']['after'][-1]['phaseWallMs'], {'baseline': 123.})
        self.assertEqual(result['phase_evidence']['after'][-1]['disabledValidationRuns'], 1)
        self.assertIsNone(result['phase_evidence']['before'][0]['phaseWallMs'])
        self.assertTrue(result['correctness_gate']['passed'])
        self.assertTrue(result['targets_met'])
        self.assertEqual(result['comparisons']['after_vs_stryker']['metrics']['wall_seconds']['paired_ratios'], [.2] * 5)
        self.assertEqual(result['comparisons']['after_vs_before']['metrics']['wall_seconds']['median'], .4)
        cpu = result['comparisons']['after_vs_stryker']['metrics']['cpu_seconds']
        self.assertNotIn('target_ratio', cpu)

    def test_target_misses_stay_valid_evidence(self):
        rows, provenance = self.evidence()
        for row in rows:
            if row['tool'] == 'oximut' and row['variant'] == 'after':
                row.update(wall_seconds=20., peak_aggregate_rss_bytes=200.)
        result = campaign.evaluate(rows, 1, 5, provenance)
        self.assertTrue(result['correctness_gate']['passed'])
        self.assertFalse(result['targets_met'])
        self.assertEqual(result['comparisons']['after_vs_stryker']['metrics']['wall_seconds']['status'], 'target missed')

    def test_every_invalid_gate_suppresses_all_ratios(self):
        for cause in ('missing', 'duplicate', 'failed', 'provenance', 'workload', 'inventory',
                      'families', 'warmup', 'nan', 'memory', 'outcome', 'cross-round-scope'):
            rows, provenance = self.evidence()
            row = rows[-2]  # last round's after/Oximut
            if cause == 'missing':
                rows.pop()
            elif cause == 'duplicate':
                rows.append(copy.deepcopy(row))
            elif cause == 'failed':
                row['success'] = False
            elif cause == 'provenance':
                row['execution_inputs']['binary_sha256'] = 'changed'
            elif cause == 'workload':
                row['workload_sha256'] = 'changed'
            elif cause == 'inventory':
                row['declared_test_identity_sha256'] = 'changed'
            elif cause == 'families':
                row['mutator_families'] = ['unrestricted']
            elif cause == 'warmup':
                row['warmup'] = True
            elif cause == 'nan':
                row['cpu_seconds'] = float('nan')
            elif cause == 'memory':
                row['memory_errors'] = ['denied']
            else:
                # Keep the subcampaign pair equal while changing it from all other rounds.
                for item in rows[-2:]:
                    if cause == 'outcome':
                        item['mutations']['exact']['outcome'] = 'survived'
                    else:
                        item['mutations']['extra'] = {'outcome': 'killed'}
            with self.subTest(cause=cause):
                result = campaign.evaluate(rows, 1, 5, provenance)
                self.assertFalse(result['correctness_gate']['passed'])
                self.assertEqual(result['comparisons'], {})

    def test_failed_attempt_and_insufficient_rounds_reject_ratios(self):
        rows, provenance = self.evidence()
        for warmups, reps, failures in [(1, 5, ['initial attempt failed']), (0, 5, []), (1, 4, [])]:
            result = campaign.evaluate(rows, warmups, reps, provenance, failures)
            self.assertFalse(result['correctness_gate']['passed'])
            self.assertEqual(result['comparisons'], {})

    def test_order_and_warmup_only_subcommands(self):
        steps = list(campaign.plan(1, 5))
        self.assertEqual([step[2] for step in steps[:4]], ['before', 'after', 'after', 'before'])
        self.assertEqual(steps[0][3], ['vitest', 'oximut', 'stryker'])
        self.assertEqual(steps[2][3], ['stryker', 'oximut', 'vitest'])
        args = SimpleNamespace(before_binary=Path('/before'), before_adapter_dir=Path('/adapter'), timeout=600)
        warmup = campaign.subcommand(args, Path('/output'), True, 'before', steps[0][3])
        self.assertEqual(warmup[warmup.index('--repetitions') + 1], '0')
        self.assertIn('--defer-ratios', warmup)
        self.assertEqual(warmup[warmup.index('--adapter') + 1], '/adapter/m2-runner.mjs')


if __name__ == '__main__':
    unittest.main()
