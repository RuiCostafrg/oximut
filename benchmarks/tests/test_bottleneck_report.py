"""The committed bottleneck report must be reproducible, data-driven and validated."""
import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'benchmarks/results/2026-11-07-bottleneck-report'
sys.path.insert(0, str(ROOT / 'benchmarks'))
import bottleneck_report  # noqa: E402
import targets  # noqa: E402

PAGES = ('index.html', 'timeline.html', 'cpu.html')


def render(data_path, directory):
    subprocess.run([sys.executable, '-I', str(ROOT / 'benchmarks/bottleneck_report.py'), 'render',
                    '--data', str(data_path), '--output-dir', str(directory)], check=True)


def campaign():
    """A synthetic qualified campaign whose summary is derived from its rows."""
    rows = []
    for index in range(6):
        for variant in ('before', 'after'):
            for tool, wall in (('vitest', 1.0), ('oximut', 5.0 + index / 10), ('stryker', 4.5 + index / 20)):
                rows.append({'campaign_round': index, 'variant': variant, 'tool': tool, 'warmup': index == 0,
                             'workload_sha256': 'w', 'wall_seconds': wall})
    summary = {'correctness_gate': {'passed': True}, 'build_integrity': {'passed': True},
               'warmup_pairs': 1, 'measured_pairs': 5, 'comparisons': {}}
    indexed = {(r['campaign_round'], r['variant'], r['tool']): r for r in rows}
    for name, ((nv, nt), (dv, dt)) in bottleneck_report.COMPARISONS.items():
        value = targets.paired_interval([(indexed[(i, nv, nt)]['wall_seconds'], indexed[(i, dv, dt)]['wall_seconds'])
                                         for i in range(1, 6)])
        summary['comparisons'][name] = {'metrics': {'wall_seconds': value}}
    return rows, summary


class BottleneckReport(unittest.TestCase):
    def test_render_reproduces_committed_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            render(REPORT / 'data.json', directory)
            for name in PAGES:
                self.assertEqual((Path(directory) / name).read_text(encoding='utf-8'),
                                 (REPORT / name).read_text(encoding='utf-8'), name)

    def test_pages_follow_the_supplied_data(self):
        data = json.loads((REPORT / 'data.json').read_text(encoding='utf-8'))
        changed = copy.deepcopy(data)
        changed['benchmark']['ratios']['after_vs_stryker']['wall_seconds']['median'] = 0.4321
        changed['controller']['workload']['tests'] = 777
        changed['profiles']['roles']['fork'] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text(json.dumps(changed), encoding='utf-8')
            render(path, directory)
            index = (Path(directory) / 'index.html').read_text(encoding='utf-8')
            self.assertIn('0.43×', index)
            self.assertIn('777 tests', index)
            # No fork profiles: shares degrade to n/a instead of failing.
            self.assertIn('n/a', index)
            self.assertIn('Partial evidence', (Path(directory) / 'cpu.html').read_text(encoding='utf-8'))

    def test_every_chart_is_named_and_has_its_own_table(self):
        for name in PAGES:
            page = (REPORT / name).read_text(encoding='utf-8')
            for target in PAGES:
                self.assertIn(f'href="{target}"', page)
            charts = re.findall(r'<svg [^>]*>', page)
            self.assertTrue(charts, name)
            for chart in charts:
                self.assertRegex(chart, r'role="img" aria-label="[^"]+"', name)
            labels = re.findall(r'<svg [^>]*aria-label="([^"]+)"', page)
            captions = re.findall(r'<caption[^>]*>Data for: ([^<]+)</caption>', page)
            # Low-contrast palette slots require a table view for every chart.
            self.assertEqual(len(captions), len(labels), name)

    def test_campaign_validation_rejects_bad_evidence(self):
        rows, summary = campaign()
        bottleneck_report.validate_campaign(rows, summary)
        with self.assertRaisesRegex(ValueError, 'missing'):
            bottleneck_report.validate_campaign(rows[:-1], summary)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            bottleneck_report.validate_campaign(rows + [rows[-1]], summary)
        tampered = copy.deepcopy(summary)
        tampered['comparisons']['after_vs_stryker']['metrics']['wall_seconds']['bootstrap_95_percent'][1] += 0.01
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            bottleneck_report.validate_campaign(rows, tampered)
        failed = copy.deepcopy(summary)
        failed['correctness_gate']['passed'] = False
        with self.assertRaisesRegex(ValueError, 'gates'):
            bottleneck_report.validate_campaign(rows, failed)


if __name__ == '__main__':
    unittest.main()
