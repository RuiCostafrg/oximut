"""Report derivation and escaping regressions using retained native evidence."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
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
        cls.data = report.collect(COMPARISON,CONTROLLER)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'comparison'
        shutil.copytree(COMPARISON,self.root)

    def update(self,name,data):
        path=self.root/name
        path.write_text(json.dumps(data)+'\n')
        lines=(self.root/'SHA256SUMS').read_text().splitlines()
        lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+name if line.endswith('  '+name) else line for line in lines]
        (self.root/'SHA256SUMS').write_text('\n'.join(lines)+'\n')

    def test_all_resource_components_phases_and_pending_costs_are_retained(self):
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
        for gate in ['correctness_gate','build_integrity']:
            data=json.loads((COMPARISON/'original-summary.json').read_text())
            data[gate]['passed']=False
            self.update('original-summary.json',data)
            with self.assertRaisesRegex(ValueError,'qualified workload'):
                report.collect(self.root,CONTROLLER)

    def test_paired_ratios_must_match_the_original_resource_observations(self):
        data=json.loads((COMPARISON/'original-summary.json').read_text())
        data['comparisons']['after_vs_stryker']['metrics']['cpu_seconds']['paired_ratios'][0] += .1
        self.update('original-summary.json',data)
        with self.assertRaisesRegex(ValueError,'paired ratios differ'):
            report.collect(self.root,CONTROLLER)

    def test_a_bad_phase_cannot_hide_behind_a_valid_median(self):
        data=json.loads((COMPARISON/'original-summary.json').read_text())
        data['phase_evidence']['after'][1]['phaseWallMs']['mutationExecution']=-1
        self.update('original-summary.json',data)
        with self.assertRaisesRegex(ValueError,'invalid phase observation'):
            report.collect(self.root,CONTROLLER)

    def test_capture_tampering_and_injected_frame_markup_are_not_silently_accepted(self):
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
        data=copy.deepcopy(self.data)
        stage={'calls':1,'wallNs':1000000,'selfUserNs':1000,'selfSystemNs':1000}
        data['accounting']={key:copy.deepcopy(stage) for key in ['sampling','discovery','identityReads','rssReads']}
        row=copy.deepcopy(data['controller']['rows'][0])
        row['peak_rss_snapshot']={'root_pid':1,'sampled_wall_seconds':.5,
                                 'processes':[{'pid':1,'ppid':0,'starttime':1,'rss_bytes':row['peak_aggregate_rss_bytes'],'comm':'fixture<script>'}]}
        data['accounting_run']={'rows':[row],'phases':data['controller']['phases']}
        page=report.render(data)
        self.assertNotIn('Pending native capture',page)
        self.assertIn('sampling; coordinator fingerprint',page)
        self.assertIn('fixture&lt;script&gt;',page)
        self.assertIn('not independent per-process peaks',page)


if __name__ == '__main__':
    unittest.main()
