"""Report derivation and escaping regressions using retained native evidence."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import performance_report as report

COMPARISON = report.ROOT / 'benchmarks/results/2026-11-05-native-control'
CONTROLLER = report.ROOT / 'benchmarks/results/2026-11-04-controller-job-identities'


class PerformanceReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Derive the shared fixture from retained qualified native evidence."""
        cls.data = report.collect(COMPARISON,CONTROLLER)

    def setUp(self):
        """Copy comparison inputs so each corruption case stays isolated."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'comparison'
        shutil.copytree(COMPARISON,self.root)

    def update(self,name,data):
        """Change one input and its digest to exercise semantic validation."""
        path=self.root/name
        path.write_text(json.dumps(data)+'\n')
        lines=(self.root/'SHA256SUMS').read_text().splitlines()
        lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+name if line.endswith('  '+name) else line for line in lines]
        (self.root/'SHA256SUMS').write_text('\n'.join(lines)+'\n')

    def test_all_resource_components_phases_and_pending_costs_are_retained(self):
        """Keep required metrics and unmeasured costs visible in the report."""
        data=self.data
        self.assertEqual(len(data['raw']),36)
        self.assertEqual(data['pairs'],5)
        self.assertEqual(len(data['controller']['intervals']['jobs']),10)
        self.assertIsNone(data['accounting'])
        page=report.render(data)
        for text in ['User / sys','Aggregate RSS','Peak / observed','resources.phaseWallMs',
                     'bootstrap 95%','Pending native capture','not additive','partial first-mutant']:
            self.assertIn(text,page)
        self.assertNotIn('<script src=',page)

    def test_failed_workload_and_build_gates_cannot_render_as_qualified(self):
        """Reject campaigns whose stored qualification or build gate fails."""
        for gate in ['correctness_gate','build_integrity']:
            data=json.loads((COMPARISON/'original-summary.json').read_text())
            data[gate]['passed']=False
            self.update('original-summary.json',data)
            with self.assertRaisesRegex(ValueError,'qualified workload'):
                report.collect(self.root,CONTROLLER)

    def test_paired_ratios_must_match_the_original_resource_observations(self):
        """Reject ratios that disagree with their raw paired observations."""
        data=json.loads((COMPARISON/'original-summary.json').read_text())
        data['comparisons']['after_vs_stryker']['metrics']['cpu_seconds']['paired_ratios'][0] += .1
        self.update('original-summary.json',data)
        with self.assertRaisesRegex(ValueError,'paired ratios differ'):
            report.collect(self.root,CONTROLLER)

    def test_a_bad_phase_cannot_hide_behind_a_valid_median(self):
        """Reject an invalid phase row even when its group median is plausible."""
        data=json.loads((COMPARISON/'original-summary.json').read_text())
        data['phase_evidence']['after'][1]['phaseWallMs']['mutationExecution']=-1
        self.update('original-summary.json',data)
        with self.assertRaisesRegex(ValueError,'invalid phase observation'):
            report.collect(self.root,CONTROLLER)

    def test_capture_tampering_and_injected_frame_markup_are_not_silently_accepted(self):
        """Detect changed evidence bytes and escape profiler frame labels."""
        path=self.root/'original-measurements.jsonl'
        path.write_text(path.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            report.collect(self.root,CONTROLLER)
        data=copy.deepcopy(self.data)
        data['controller']['frames'][0]['function']='</td><script>alert(1)</script>'
        page=report.render(data)
        self.assertNotIn('<script>alert(1)',page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',page)

    def test_accounting_and_same_sample_process_memory_can_replace_pending_fields(self):
        """Display measured sampling costs and escaped same-sample RSS details."""
        data=copy.deepcopy(self.data)
        stage={'calls':1,'wallNs':1000000,'selfUserNs':1000,'selfSystemNs':1000}
        data['accounting']={key:copy.deepcopy(stage) for key in ['sampling','discovery','identityReads','rssReads']}
        row=copy.deepcopy(data['controller']['rows'][0])
        row['peak_rss_snapshot']={'root_pid':1,'sampled_wall_seconds':.5,
                                 'processes':[{'pid':1,'ppid':0,'starttime':1,'rss_bytes':row['peak_aggregate_rss_bytes'],'comm':'fixture<script>'}]}
        data['accounting_run']={'rows':[row],'phases':data['controller']['phases']}
        page=report.render(data)
        self.assertNotIn('Pending native capture',page)
        self.assertIn('stage totals cover only sampling;',page)
        self.assertIn('fixture&lt;script&gt;',page)
        self.assertIn('not independent per-process peaks',page)

    def test_native_accounting_is_revalidated_and_clock_skew_is_visible(self):
        """Revalidate retained counters and disclose their CPU clock limitation."""
        accounting=report.ROOT/'benchmarks/results/2026-11-05-accounting-profile'
        comparison=report.ROOT/'benchmarks/results/2026-11-05-accounting-control'
        data=report.collect(comparison,CONTROLLER,accounting)
        self.assertEqual(data['accounting']['sampling']['calls'],106)
        page=report.render(data)
        self.assertIn('Clock-boundary limitation observed in identityReads',page)
        self.assertIn('42006490326',page)
        self.assertIn('node-MainThread',page)

    def test_native_precise_data_and_rejected_worker_capture_keep_separate_scopes(self):
        """Keep scored ratios, precise accounting and rejected worker profiles distinct."""
        base=report.ROOT/'benchmarks/results'
        data=report.collect(base/'2026-11-05-precise-accounting-control',CONTROLLER,
                            base/'2026-11-05-precise-accounting-profile',base/'2026-11-05-exit-time-profile-rejected')
        self.assertEqual(data['accounting']['clockVersion'],2)
        self.assertEqual(data['rejected_node']['native_run'],42058984932)
        self.assertEqual(data['source']['benchmark_run'],42058759907)
        page=report.render(data)
        self.assertIn('failed profile validation',page)
        self.assertIn('No worker CPU attribution is claimed',page)
        self.assertIn('Budget intervals through resource snapshots</td><td>744.762',page)
        self.assertIn('Sampling windows</td><td>704.948',page)
        self.assertIn('Outside sampling, within Budget intervals</td><td>39.814',page)

    def test_rejected_capture_cannot_silently_become_a_qualified_profile(self):
        """Reject changed capture status even when its file digest is updated."""
        base=report.ROOT/'benchmarks/results'
        root=Path(self.temp.name)/'rejected'
        shutil.copytree(base/'2026-11-05-exit-time-profile-rejected',root)
        path=root/'diagnostic.json';record=json.loads(path.read_text());record['passed']=True
        path.write_text(json.dumps(record)+'\n')
        manifest=root/'SHA256SUMS'
        manifest.write_text('\n'.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  diagnostic.json' if line.endswith('  diagnostic.json') else line for line in manifest.read_text().splitlines())+'\n')
        with self.assertRaisesRegex(ValueError,'rejected Node capture status differs'):
            report.collect(base/'2026-11-05-precise-accounting-control',CONTROLLER,
                           base/'2026-11-05-precise-accounting-profile',root)

    def test_rejected_capture_resources_still_require_successful_exact_measurements(self):
        """Reject failed observations even when normalized test results still match."""
        base=report.ROOT/'benchmarks/results'
        original=base/'2026-11-05-exit-time-profile-rejected'
        root=Path(self.temp.name)/'rejected-resources';shutil.copytree(original,root)
        source=json.loads((base/'2026-11-05-precise-accounting-control/evidence-source.json').read_text())
        template=[json.loads(line) for line in (original/'qualification/measurements.jsonl').read_text().splitlines()]
        for key,value in [('success',False),('timed_out',True),('memory_errors',['injected']),
                          ('exit_code',1),('memory_method','proc-stat'),('spawn_error','injected'),
                          ('descendant_cleanup_required',True),('cleanup_error','injected'),
                          ('cleanup_error',None),('validation_error','injected')]:
            for row_index in range(len(template)):
                rows=copy.deepcopy(template);rows[row_index][key]=value
                path=root/'qualification/measurements.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                manifest=root/'SHA256SUMS';name='qualification/measurements.jsonl'
                manifest.write_text('\n'.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+name if line.endswith('  '+name) else line for line in manifest.read_text().splitlines())+'\n')
                with self.subTest(field=key,row=row_index),self.assertRaisesRegex(ValueError,'invalid rejected Node workload result'):
                    report.rejected_node_capture(root,source)

    def test_precise_cpu_is_distinct_from_raw_splits_and_budget_remainder(self):
        """Label precise totals and the bounded CLI remainder without worker attribution."""
        accounting=report.ROOT/'benchmarks/results/2026-11-05-accounting-profile'
        comparison=report.ROOT/'benchmarks/results/2026-11-05-accounting-control'
        data=report.collect(comparison,CONTROLLER,accounting)
        diag=data['accounting'];diag['clockVersion']=2;diag['threadCpuSinceBudgetStartNs']=3_000_000
        for name in ('sampling','discovery','identityReads','rssReads'): diag[name]['selfCpuNs']=100_000
        diag['sampling']['selfCpuNs']=2_000_000
        page=report.render(data)
        self.assertIn('Precise self CPU ms',page)
        self.assertIn('Budget intervals through resource snapshots',page)
        self.assertIn('Outside sampling, within Budget intervals</td><td>1.000',page)
        self.assertIn('not worker CPU',page)
        self.assertNotIn('Legacy capture: precise thread CPU',page)

    def test_versioned_precise_capture_is_revalidated_before_rendering(self):
        """Accept complete version 2 input and reject missing precise fields."""
        original=report.ROOT/'benchmarks/results/2026-11-05-accounting-profile'
        comparison=report.ROOT/'benchmarks/results/2026-11-05-accounting-control'
        root=Path(self.temp.name)/'precise'
        shutil.copytree(original,root)
        record=json.loads((root/'diagnostic.json').read_text());record['accounting_clock_version']=2
        (root/'diagnostic.json').write_text(json.dumps(record)+'\n')
        report_path=root/'qualification/00-oximut/report.json'
        native=json.loads(report_path.read_text());diag=native['resources']['accountingDiagnostics']
        diag.update(clockVersion=2,threadCpuSinceBudgetStartNs=1_000_000,threadCpuFailures=0)
        for name in ('sampling','discovery','identityReads','rssReads'):
            diag[name].update(selfCpuNs=100_000,selfCpuFailures=0)
        diag['sampling']['selfCpuNs']=500_000
        for missing in (False,True):
            if missing: del diag['rssReads']['selfCpuNs']
            report_path.write_text(json.dumps(native)+'\n')
            names=[line.split('  ',1)[1] for line in (root/'SHA256SUMS').read_text().splitlines()]
            (root/'SHA256SUMS').write_text(''.join(hashlib.sha256((root/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in names))
            if missing:
                with self.assertRaisesRegex(ValueError,'accounting evidence incomplete'):
                    report.collect(comparison,CONTROLLER,root)
            else:
                data=report.collect(comparison,CONTROLLER,root)
                self.assertEqual(data['accounting']['clockVersion'],2)
                self.assertIn('CLOCK_THREAD_CPUTIME_ID',report.render(data))

    def test_ordered_but_invented_confidence_bounds_are_rejected(self):
        """Require confidence bounds to reproduce the deterministic bootstrap."""
        data=json.loads((COMPARISON/'original-summary.json').read_text())
        metric=data['comparisons']['after_vs_stryker']['metrics']['cpu_seconds']
        metric['bootstrap_95_percent']=[metric['median']-.001,metric['median']+.001]
        self.update('original-summary.json',data)
        with self.assertRaisesRegex(ValueError,'bootstrap bounds differ'):
            report.collect(self.root,CONTROLLER)

    def test_every_consumed_input_requires_a_manifest_entry(self):
        """Require digests for every consumed comparison input."""
        for name in ('original-summary.json','original-measurements.jsonl','original-metadata.json','evidence-source.json'):
            lines=(COMPARISON/'SHA256SUMS').read_text().splitlines()
            (self.root/'SHA256SUMS').write_text('\n'.join(line for line in lines if not line.endswith('  '+name))+'\n')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'unhashed evidence'):
                report.collect(self.root,CONTROLLER)

    def test_controller_and_accounting_consumed_inputs_require_manifest_entries(self):
        """Require complete manifests for both diagnostic evidence roots."""
        comparison=report.ROOT/'benchmarks/results/2026-11-05-accounting-control'
        accounting=report.ROOT/'benchmarks/results/2026-11-05-accounting-profile'
        for original,required in [(CONTROLLER,['boundary-intervals.json','qualification/summary.json',
                'qualification/measurements.jsonl','qualification/00-oximut/report.json']+
                [str(p.relative_to(CONTROLLER)) for p in (CONTROLLER/'profiles').iterdir() if p.is_file()]),
                (accounting,['diagnostic.json','build-metadata.json','evidence-source.json',
                            'qualification/metadata.json','qualification/summary.json',
                            'qualification/measurements.jsonl','qualification/00-oximut/report.json'])]:
            root=Path(self.temp.name)/original.name
            shutil.copytree(original,root)
            lines=(original/'SHA256SUMS').read_text().splitlines()
            for name in required:
                (root/'SHA256SUMS').write_text('\n'.join(line for line in lines if not line.endswith('  '+name))+'\n')
                controller_arg,accounting_arg=(root,accounting) if original==CONTROLLER else (CONTROLLER,root)
                with self.subTest(root=original.name,name=name),self.assertRaisesRegex(ValueError,'unhashed evidence'):
                    report.collect(comparison,controller_arg,accounting_arg)

    def test_older_comparison_renders_without_an_incompatible_default_capture(self):
        """Render historical controls without joining a newer accounting build."""
        output=Path(self.temp.name)/'historical.html'
        result=subprocess.run([sys.executable,report.__file__,'--comparison',str(COMPARISON),
                               '--output',str(output)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Pending native capture',output.read_text())

    def test_every_ratio_chart_exposes_values_outside_its_svg(self):
        """Keep all ratio values available outside inaccessible SVG text."""
        for key in ('after_vs_stryker','after_vs_before','before_vs_stryker'):
            chart=report.ratio_chart(self.data,key)
            accessible=re.sub(r'<svg\b.*?</svg>','',chart,flags=re.S)
            with self.subTest(comparison=key):
                for metric in self.data['comparisons'][key]['metrics'].values():
                    for value in (metric['median'],*metric['bootstrap_95_percent']):
                        self.assertIn(f'{value:.3f}',accessible)

    def test_workload_is_requalified_instead_of_trusting_summary_booleans(self):
        """Recheck raw workload identities despite passing stored gate booleans."""
        path=self.root/'original-measurements.jsonl'
        rows=[json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['workload_sha256']='0'*64
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        manifest=self.root/'SHA256SUMS'
        manifest.write_text('\n'.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  original-measurements.jsonl' if line.endswith('  original-measurements.jsonl') else line for line in manifest.read_text().splitlines())+'\n')
        with self.assertRaisesRegex(ValueError,'raw workload requalification'):
            report.collect(self.root,CONTROLLER)

    def test_accounting_from_another_engine_build_cannot_be_joined_to_control(self):
        """Reject qualified accounting evidence from a different candidate build."""
        accounting=report.ROOT/'benchmarks/results/2026-11-05-accounting-profile'
        with self.assertRaisesRegex(ValueError,'accounting source differs'):
            report.collect(COMPARISON,CONTROLLER,accounting)


if __name__ == '__main__':
    unittest.main()
