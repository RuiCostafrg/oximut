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
        rows, provenance, policy = self.strict_evidence()
        rows[-2]['resources']['phaseWallMs']['originalBaseline'] = 123.
        result = campaign.evaluate(rows, 1, 5, provenance, **policy)
        self.assertEqual(result['phase_evidence']['after'][-1]['phaseWallMs']['originalBaseline'], 123.)
        self.assertEqual(len(result['phase_evidence']['after'][-1]['disabledValidationRuns']), 5)
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


    def strict_evidence(self):
        rows, provenance = self.evidence()
        manifest = json.loads((campaign.workload.CORPUS / 'workload.json').read_text())
        commits = {'before': campaign.BASELINE, 'after': 'a' * 40}
        metadata = {f'{v}_commit': commits[v] for v in campaign.VARIANTS}
        metadata.update(binary_sha256={v: provenance[v]['binary_sha256'] for v in campaign.VARIANTS},
                        cargo_target_dirs={'before': '/targets/before', 'after': '/targets/after'})
        for row in rows:
            if row['tool'] == 'oximut' and row['variant'] == 'after':
                row['cwd'] = '/projects/after'
                row['resources'] = {
                    'phaseWallMs': {key: 1. for key in campaign.PHASES},
                    'disabledValidationRuns': [{'file': f'/projects/after/{file}', 'wallMs': 1.}
                                               for file in manifest['mutate']]}
        return rows, provenance, dict(require_build_integrity=True, build_metadata=metadata,
                                     candidate_commits=commits)

    def test_strict_build_and_complete_after_phases_qualify_with_old_before_binary(self):
        rows, provenance, policy = self.strict_evidence()
        result = campaign.evaluate(rows, 1, 5, provenance, **policy)
        self.assertTrue(result['correctness_gate']['passed'])
        self.assertTrue(result['build_integrity']['passed'])
        self.assertIsNone(result['phase_evidence']['before'][0]['phaseWallMs'])

    def test_build_metadata_and_expected_executable_divergence_gate_all_ratios(self):
        for cause in ('equal-binaries', 'missing', 'malformed', 'commit', 'hash',
                      'missing-directories', 'same-directory', 'nested-directory', 'relative-directory',
                      'aliased-directory'):
            rows, provenance, policy = self.strict_evidence()
            metadata = policy['build_metadata']
            if cause == 'equal-binaries':
                provenance['after']['binary_sha256'] = provenance['before']['binary_sha256']
                metadata['binary_sha256']['after'] = provenance['before']['binary_sha256']
                for row in rows:
                    row['execution_inputs'] = copy.deepcopy(provenance[row['variant']])
                # An adapter-only experiment may intentionally use one executable.
                generic = campaign.evaluate(rows, 1, 5, provenance)
                self.assertTrue(generic['correctness_gate']['passed'])
            elif cause == 'missing':
                policy['build_metadata'] = None
            elif cause == 'malformed':
                policy['build_metadata'] = []
            elif cause == 'commit':
                metadata['after_commit'] = 'wrong'
            elif cause == 'hash':
                metadata['binary_sha256']['after'] = 'wrong'
            elif cause == 'missing-directories':
                metadata.pop('cargo_target_dirs')
            elif cause == 'same-directory':
                metadata['cargo_target_dirs']['after'] = '/targets/before'
            elif cause == 'nested-directory':
                metadata['cargo_target_dirs']['after'] = '/targets/before/subdir'
            elif cause == 'relative-directory':
                metadata['cargo_target_dirs']['after'] = 'relative'
            else:
                metadata['cargo_target_dirs']['after'] = '/targets/after/../before'
            with self.subTest(cause=cause):
                result = campaign.evaluate(rows, 1, 5, provenance, **policy)
                self.assertFalse(result['correctness_gate']['passed'])
                self.assertFalse(result['build_integrity']['passed'])
                self.assertEqual(result['comparisons'], {})

    def test_missing_or_invalid_after_phases_including_warmup_suppress_all_ratios(self):
        causes = [f'missing:{key}' for key in campaign.PHASES]
        causes += ['warmup', 'resources-list', 'resources-string', 'phases-list', 'missing-runs',
                   'runs-count', 'missing-file', 'duplicate-file', 'extra-file', 'malformed-run']
        for bad in (True, None, '1', -1, float('nan'), float('inf'), -float('inf'), 10 ** 400):
            causes.extend([('duration', bad), ('run-duration', bad)])
        for cause in causes:
            rows, provenance, policy = self.strict_evidence()
            row = rows[4] if cause == 'warmup' else rows[-2]
            resources = row['resources']
            if isinstance(cause, tuple):
                if cause[0] == 'duration':
                    resources['phaseWallMs']['originalBaseline'] = cause[1]
                else:
                    resources['disabledValidationRuns'][0]['wallMs'] = cause[1]
            elif cause.startswith('missing:'):
                resources['phaseWallMs'].pop(cause.split(':', 1)[1])
            elif cause == 'warmup':
                row.pop('resources')
            elif cause == 'resources-list':
                row['resources'] = [1]
            elif cause == 'resources-string':
                row['resources'] = 'bad'
            elif cause == 'phases-list':
                resources['phaseWallMs'] = []
            elif cause == 'missing-runs':
                resources.pop('disabledValidationRuns')
            elif cause == 'runs-count':
                resources['disabledValidationRuns'] = 5
            elif cause == 'missing-file':
                resources['disabledValidationRuns'].pop()
            elif cause == 'duplicate-file':
                resources['disabledValidationRuns'][1] = resources['disabledValidationRuns'][0]
            elif cause == 'extra-file':
                resources['disabledValidationRuns'].append({'file': '/projects/after/src/extra.ts', 'wallMs': 1})
            elif cause == 'malformed-run':
                resources['disabledValidationRuns'][0] = None
            with self.subTest(cause=cause):
                result = campaign.evaluate(rows, 1, 5, provenance, **policy)
                self.assertFalse(result['correctness_gate']['passed'])
                self.assertEqual(result['comparisons'], {})

    def test_failed_build_preflight_retains_evidence_without_running_commands(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'binary'
            binary.write_bytes(b'same executable')
            adapter = root / 'adapter'
            adapter.mkdir()
            (adapter / 'm2-runner.mjs').write_text('// unused')
            output = root / 'result'
            provenance = {'binary_sha256': 'same', 'adapter_source_sha256': {}}
            with (patch.object(campaign.workload.measurement, 'metadata', return_value={'versions': {}}),
                  patch.object(campaign.workload, 'execution_inputs', return_value=provenance),
                  patch.object(campaign.subprocess, 'run') as command):
                result = campaign.main(['--before-binary', str(binary), '--after-binary', str(binary),
                                        '--before-adapter-dir', str(adapter), '--after-adapter-dir', str(adapter),
                                        '--after-commit', 'a' * 40, '--require-build-integrity',
                                        '--output', str(output)])
            self.assertEqual(result, 1)
            command.assert_not_called()
            self.assertTrue((output / 'metadata.json').is_file())
            self.assertEqual((output / 'measurements.jsonl').read_text(), '')
            self.assertEqual(json.loads((output / 'attempts.json').read_text()), [])
            summary = json.loads((output / 'summary.json').read_text())
            self.assertFalse(summary['correctness_gate']['passed'])
            self.assertEqual(summary['comparisons'], {})
            for label in ('missing', 'invalid-json'):
                metadata_file = root / f'{label}.json'
                if label == 'invalid-json':
                    metadata_file.write_text('{not json')
                invalid_output = root / label
                with (patch.object(campaign.workload.measurement, 'metadata', return_value={'versions': {}}),
                      patch.object(campaign.workload, 'execution_inputs', return_value=provenance),
                      patch.object(campaign.subprocess, 'run') as command):
                    result = campaign.main(['--before-binary', str(binary), '--after-binary', str(binary),
                                            '--before-adapter-dir', str(adapter), '--after-adapter-dir', str(adapter),
                                            '--after-commit', 'a' * 40, '--build-metadata', str(metadata_file),
                                            '--output', str(invalid_output)])
                self.assertEqual(result, 1)
                command.assert_not_called()
                self.assertIsNotNone(json.loads((invalid_output / 'metadata.json').read_text())['build_metadata_error'])
                invalid = json.loads((invalid_output / 'summary.json').read_text())
                self.assertFalse(invalid['correctness_gate']['passed'])
                self.assertEqual(invalid['comparisons'], {})


if __name__ == '__main__':
    unittest.main()
