"""Accounting evidence corruption checks; no builds or performance claims."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile_native as diagnostic


class AccountingCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / '00-oximut').mkdir()
        stage = {'calls': 3, 'wallNs': 40, 'selfUserNs': 10,
                 'selfSystemNs': 5, 'cpuFailures': 0, 'timingFailures': 0}
        self.report = {
            'diagnostics': ['native coordinator: fixture'],
            'resources': {'samples': 3, 'exhaustion': None, 'accountingDiagnostics': {
                'scope': 'CLI sampling thread only', 'evidence': 'complete counters', 'timingNote': 'nested',
                **{key: copy.deepcopy(stage) for key in ('sampling','discovery','identityReads','rssReads')},
            }},
        }

    def capture(self, report):
        (self.root / '00-oximut/report.json').write_text(json.dumps(report))
        rows=[{'tool':tool,'wall_seconds':1.0,'peak_process_count':1,'observed_process_count':1,'peak_aggregate_rss_bytes':100,
               'peak_rss_snapshot':{'root_pid':1,'sampled_wall_seconds':.5,
                                    'processes':[{'pid':1,'ppid':0,'starttime':1,'rss_bytes':100,'comm':tool}]}}
              for tool in ['oximut','stryker','vitest']]
        (self.root/'measurements.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return diagnostic.accounting_capture_inventory(self.root)

    def test_complete_accounting_is_not_reported_as_node_identity_evidence(self):
        result = self.capture(self.report)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['observed'], [])
        self.assertEqual(result['accounting_diagnostics'], self.report['resources']['accountingDiagnostics'])
        summary = diagnostic.compact_summary({'captures': result, 'node_capture_mode': 'rust-accounting-self'})
        self.assertIsNone(summary['observed_identity_count'])
        self.assertEqual(summary['accounting_diagnostics'], result['accounting_diagnostics'])

    def test_missing_negative_noninteger_null_and_failure_totals_are_rejected(self):
        for stage in ('sampling','discovery','identityReads','rssReads'):
            for key in ('calls','wallNs','selfUserNs','selfSystemNs','cpuFailures','timingFailures'):
                for invalid in (None, True, -1, 1.5, 2**64):
                    with self.subTest(stage=stage, key=key, value=invalid):
                        report = copy.deepcopy(self.report)
                        report['resources']['accountingDiagnostics'][stage][key] = invalid
                        self.assertTrue(self.capture(report)['errors'])
            for key in ('cpuFailures','timingFailures'):
                report = copy.deepcopy(self.report)
                report['resources']['accountingDiagnostics'][stage][key] = 1
                self.assertTrue(self.capture(report)['errors'])
            report = copy.deepcopy(self.report)
            del report['resources']['accountingDiagnostics'][stage]['wallNs']
            self.assertTrue(self.capture(report)['errors'])

    def test_missing_profile_empty_stages_and_mismatched_sample_counts_are_rejected(self):
        for key in ('samples','accountingDiagnostics'):
            report = copy.deepcopy(self.report)
            del report['resources'][key]
            self.assertTrue(self.capture(report)['errors'])
        for stage in ('sampling','discovery','identityReads','rssReads'):
            report = copy.deepcopy(self.report)
            report['resources']['accountingDiagnostics'][stage]['calls'] = 0
            self.assertTrue(self.capture(report)['errors'])
        for stage in ('sampling','discovery'):
            report = copy.deepcopy(self.report)
            report['resources']['accountingDiagnostics'][stage]['calls'] += 1
            self.assertTrue(self.capture(report)['errors'])
        report = copy.deepcopy(self.report)
        report['resources']['accountingDiagnostics']['rssReads']['calls'] += 1
        self.assertTrue(self.capture(report)['errors'])

    def test_bad_native_or_resource_evidence_and_malformed_report_fail_closed(self):
        report = copy.deepcopy(self.report)
        report['diagnostics'] = []
        self.assertTrue(self.capture(report)['errors'])
        report = copy.deepcopy(self.report)
        report['resources']['exhaustion'] = 'identity mismatch'
        self.assertTrue(self.capture(report)['errors'])
        (self.root / '00-oximut/report.json').write_text('{')
        self.assertTrue(diagnostic.accounting_capture_inventory(self.root)['errors'])

    def test_memory_peak_requires_matching_sum_and_unique_verified_identities(self):
        for fault in ['missing','sum','duplicate','identity']:
            self.capture(self.report)
            path=self.root/'measurements.jsonl'
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            if fault=='missing': del rows[0]['peak_rss_snapshot']
            elif fault=='sum': rows[0]['peak_rss_snapshot']['processes'][0]['rss_bytes']=99
            elif fault=='duplicate':
                rows[0]['peak_process_count']=2
                rows[0]['observed_process_count']=2
                rows[0]['peak_aggregate_rss_bytes']=200
                rows[0]['peak_rss_snapshot']['processes'] *= 2
            else: rows[0]['peak_rss_snapshot']['processes'][0]['starttime']=True
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.subTest(fault=fault):
                self.assertTrue(diagnostic.accounting_capture_inventory(self.root)['errors'])

    def test_peak_processes_must_have_an_acyclic_start_ordered_chain_to_the_root(self):
        for fault in ('root','unrelated','cycle','start'):
            self.capture(self.report)
            path=self.root/'measurements.jsonl'
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            row=rows[0]
            row.update(peak_process_count=3,observed_process_count=3,peak_aggregate_rss_bytes=300)
            snapshot=row['peak_rss_snapshot']
            snapshot['processes'] += [
                {'pid':2,'ppid':1,'starttime':2,'rss_bytes':100,'comm':'child'},
                {'pid':3,'ppid':2,'starttime':3,'rss_bytes':100,'comm':'grandchild'}]
            if fault=='root': snapshot['root_pid']=4
            elif fault=='unrelated': snapshot['processes'][2]['ppid']=99
            elif fault=='cycle': snapshot['processes'][1]['ppid']=3
            else: snapshot['processes'][1]['starttime']=4
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            with self.subTest(fault=fault):
                self.assertTrue(diagnostic.accounting_capture_inventory(self.root)['errors'])


if __name__ == '__main__':
    unittest.main()
