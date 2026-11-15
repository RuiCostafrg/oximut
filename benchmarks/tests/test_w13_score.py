"""Scoring accounting/order/failure tests; no product builds or benchmarks."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import w13_score as score
import w13_score_summary as summary
import test_compare_candidates as campaign_tests


class ScoreTests(unittest.TestCase):
    def evidence(self):
        rows, provenance, policy = campaign_tests.CampaignTests().strict_evidence()
        setups = []
        for row in rows:
            row.update(exit_code=0, cpu_user_seconds=row['cpu_seconds'] * .75,
                       cpu_system_seconds=row['cpu_seconds'] * .25, observation_file='execution.json')
            if row['tool'] == 'oximut':
                if 'resources' not in row:
                    row['cwd'] = '/projects/before'
                    row['resources'] = copy.deepcopy(next(r['resources'] for r in rows if 'resources' in r))
                row['resources'].update(effectiveWorkers=2, admissionPauses=0)
        for i in range(6):
            setups.append(dict(campaign_round=i, exit_code=0, wall_seconds=1. + i,
                cpu_seconds=2., cpu_user_seconds=1.5, cpu_system_seconds=.5,
                peak_aggregate_rss_bytes=100., memory_method='sum-live-smaps_rollup-rss',
                observation_file=f'setup-{i}.json'))
        return rows, setups, provenance, policy['build_metadata'], policy['candidate_commits']

    def test_all_six_tool_orders_and_balanced_variant_order(self):
        plan = list(score.trial_plan())
        self.assertEqual(len(plan), 12)
        self.assertEqual(len({tuple(p[3]) for p in plan}), 6)
        self.assertEqual([p[2] for p in plan[::2]], ['before', 'after'] * 3)
        for tool in ('vitest', 'oximut', 'stryker'):
            self.assertEqual([sum(p[3][position] == tool for p in plan) for position in range(3)], [4, 4, 4])
        self.assertEqual(sum(p[1] for p in plan), 2)

    def test_full_cost_totals_are_formed_per_pair_before_ratios(self):
        rows, setups, provenance, metadata, commits = self.evidence()
        results, cold = score.evaluate(rows, setups, provenance, metadata, commits, 2, 'colord')
        self.assertTrue(results['prepared']['correctness_gate']['passed'], results)
        self.assertTrue(results['first_use']['correctness_gate']['passed'], results)
        prepared = results['prepared']['comparisons']['after_vs_stryker']['metrics']
        first = results['first_use']['comparisons']['after_vs_stryker']['metrics']
        self.assertEqual(prepared['wall_seconds']['paired_ratios'], [.2] * 5)
        self.assertEqual(first['wall_seconds']['paired_ratios'], [.4, .5, .6, .7, .8])
        self.assertEqual(first['peak_aggregate_rss_bytes']['paired_ratios'], [1.] * 5)
        self.assertEqual(first['cpu_seconds']['paired_ratios'], [.6] * 5)
        self.assertFalse(results['first_use']['targets_met'])
        candidate = next(r for r in cold if r['variant'] == 'after' and r['tool'] == 'oximut')
        self.assertEqual(candidate['cpu_user_seconds'], 4.5)
        self.assertEqual(candidate['cpu_system_seconds'], 1.5)
        self.assertEqual(candidate['resources'], next(r['resources'] for r in rows if r['variant'] == 'after' and r['tool'] == 'oximut'))

    def test_setup_resource_or_exit_failure_suppresses_both_views(self):
        for field, value in [('exit_code', 2), ('memory_errors', ['restricted']), ('timed_out', True),
                             ('descendant_cleanup_required', True), ('wall_seconds', float('nan'))]:
            rows, setups, provenance, metadata, commits = self.evidence()
            setups[-1][field] = value
            results, _ = score.evaluate(rows, setups, provenance, metadata, commits, 2, 'colord')
            for result in results.values():
                self.assertFalse(result['correctness_gate']['passed'], field)
                self.assertEqual(result['comparisons'], {})

    def test_missing_setup_or_inventory_build_phase_failure_suppresses_ratios(self):
        for cause in ('setup', 'inventory', 'build', 'phase', 'failure'):
            rows, setups, provenance, metadata, commits = self.evidence()
            failures = []
            if cause == 'setup': setups.pop()
            elif cause == 'inventory': rows[-2]['declared_test_identity_sha256'] = 'changed'
            elif cause == 'build': metadata['after_commit'] = 'other'
            elif cause == 'phase': rows[-2]['resources']['disabledValidationRuns'] = []
            else: failures.append('prior invariant failure')
            results, _ = score.evaluate(rows, setups, provenance, metadata, commits, 2, 'colord', failures)
            for result in results.values():
                self.assertFalse(result['correctness_gate']['passed'], cause)
                self.assertEqual(result['comparisons'], {})

    def test_mode_and_environment_preflight_reject_unsupported_paths(self):
        for workers, extra in ((1, []), (3, []), (2, ['--no-shared-controller']), (4, ['--isolation', 'fresh-thread'])):
            with self.assertRaisesRegex(ValueError, 'only workers2/4'):
                score.require_mode(workers, extra)
        for variable in (score.HOOK, 'NODE_OPTIONS', 'OXIMUT_PROFILE_MEMORY', 'OXIMUT_PROFILE_ACCOUNTING'):
            with self.assertRaisesRegex(ValueError, 'inherited'):
                score.clean_env({variable: '1'})
        env = score.clean_env({'PATH': '/bin'})
        self.assertNotIn(score.HOOK, env)
        self.assertEqual(env['CI'], 'true')

    def test_gap_distribution_reports_observed_not_requested_interval(self):
        result = score.gap_summary([{'sample_gaps_seconds': [.052, .138, .08]}])
        self.assertEqual(result['median'], .08)
        self.assertEqual(result['p95'], .138)
        self.assertEqual(result['requested_interval_seconds'], .02)

    def test_hook_is_only_on_measured_candidate_invocation(self):
        for variant in score.campaign.VARIANTS:
            for tool in ('vitest', 'oximut', 'stryker'):
                candidate = (variant, tool) == ('after', 'oximut')
                env = score.command_env({}, Path('/report'), variant, tool, Path('/prepared') if candidate else None)
                self.assertEqual(score.HOOK in env, candidate)
                if not candidate:
                    with self.assertRaisesRegex(ValueError, 'only allowed'):
                        score.command_env({}, Path('/report'), variant, tool, Path('/prepared'))
        with self.assertRaisesRegex(ValueError, 'measured fresh preparation'):
            score.command_env({}, Path('/report'), 'after', 'oximut')

    def test_real_short_command_retains_same_sampling_gaps_without_profiler(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = score.workload.measurement.run_command(
                [sys.executable, '-c', 'import time; time.sleep(.1)'], root, root / 'out.log',
                record_sample_gaps=True)
        self.assertTrue(row['success'], row)
        self.assertEqual(len(row['sample_gaps_seconds']), row['samples'] - 1)
        self.assertEqual(max(row['sample_gaps_seconds']), row['max_sample_gap_seconds'])
        self.assertNotIn('peak_rss_snapshot', row)

    def test_global_failure_suppresses_previously_completed_config_ratios(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows, setups, provenance, metadata, commits = self.evidence()
            evaluations, _ = score.evaluate(rows, setups, provenance, metadata, commits, 2, 'colord')
            for scope, workers in summary.CONFIGS:
                directory = root / f'{scope}-{workers}w'
                directory.mkdir()
                (directory / 'score.json').write_text(json.dumps({'status': 'completed', 'head': 'fixed', 'evaluations': evaluations}))
            self.assertTrue(summary.summarize(root)['correctness_gate']['passed'])
            (root / 'colord-scale-4w/score.json').unlink()
            failed = summary.summarize(root)
            self.assertFalse(failed['correctness_gate']['passed'])
            for config in failed['configs'].values():
                for evaluation in config['evaluations'].values():
                    self.assertEqual(evaluation['comparisons'], {})


if __name__ == '__main__':
    unittest.main()
