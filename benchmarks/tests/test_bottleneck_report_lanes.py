"""The committed shared-lane bottleneck report must be reproducible, data-driven and validated."""
import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'benchmarks/results/2026-11-10-bottleneck-report'
sys.path.insert(0, str(ROOT / 'benchmarks'))
import bottleneck_report_lanes as lanes  # noqa: E402

PAGES = ('index.html', 'timeline.html', 'cpu.html')


def render(data_path, directory):
    return subprocess.run([sys.executable, '-I', str(ROOT / 'benchmarks/bottleneck_report_lanes.py'), 'render',
                           '--data', str(data_path), '--output-dir', str(directory)],
                          capture_output=True, text=True)


def load():
    return json.loads((REPORT / 'data.json').read_text(encoding='utf-8'))


def render_changed(changed):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'data.json'
        path.write_text(json.dumps(changed), encoding='utf-8')
        result = render(path, directory)
        pages = {name: (Path(directory) / name).read_text(encoding='utf-8')
                 for name in PAGES if (Path(directory) / name).exists()}
    return result, pages


class LaneReport(unittest.TestCase):
    def test_render_reproduces_committed_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            result = render(REPORT / 'data.json', directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in PAGES:
                self.assertEqual((Path(directory) / name).read_text(encoding='utf-8'),
                                 (REPORT / name).read_text(encoding='utf-8'), name)

    def test_other_data_schemas_are_refused_without_writing_pages(self):
        changed = load()
        changed['schema'] = 1
        result, pages = render_changed(changed)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(pages, {})

    def test_pages_follow_the_supplied_data(self):
        changed = load()
        changed['benchmark']['ratios']['after_vs_stryker']['wall_seconds']['median'] = 0.4321
        changed['controller']['workload']['tests'] = 777
        changed['profiles']['roles']['fork'] = []
        result, pages = render_changed(changed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('0.432×', pages['index.html'])
        self.assertIn('777 tests', pages['index.html'])
        self.assertIn('Partial evidence', pages['cpu.html'])

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
            self.assertEqual(captions, labels, name)

    def test_timeline_table_has_a_row_per_fork(self):
        page = (REPORT / 'timeline.html').read_text(encoding='utf-8')
        svg = re.search(r'<svg .*?</svg>', page, re.S).group(0)
        chart_rows = re.findall(r'<text class="lbl"[^>]*>([^<]+)</text>', svg)
        table = re.search(r'<caption[^>]*>Data for: [^<]+</caption>(.*?)</table>', page, re.S).group(1)
        self.assertEqual(re.findall(r'<tr><td>([^<]+)</td>', table), chart_rows)
        data = load()
        self.assertEqual(len(chart_rows), sum(len(j['forks']) for j in data['controller']['jobs']))

    def test_estimates_follow_the_capture_timestamps(self):
        data = load()
        analysis = lanes.analyse(data)
        by_name = {name: ms for name, ms, _ in analysis['estimates']}
        self.assertEqual([ms for _, ms, _ in analysis['estimates']],
                         sorted((ms for _, ms, _ in analysis['estimates']), reverse=True))
        # The fork pool can hide every job's bootstrap but the first one's.
        self.assertAlmostEqual(by_name['Fresh-fork bootstrap on the critical path'],
                               sum(analysis['critical_boot'][1:]))
        # Overlapping the instrumented baseline is bounded by that job's whole wall time.
        self.assertAlmostEqual(by_name['Instrumented baseline runs after the original baseline'],
                               analysis['baselines'][1]['end'] - analysis['baselines'][1]['start'])
        batches = analysis['mutants']
        self.assertGreater(len(batches), 2)
        self.assertLess(batches[-1]['lanes'], data['concurrency'])
        # Slower forks in the cold batch must raise its estimate and nothing else.
        slower = copy.deepcopy(data)
        first = next(j for j in slower['controller']['jobs'] if j['lanes'])
        first['end'] += 100
        again = {name: ms for name, ms, _ in lanes.analyse(slower)['estimates']}
        self.assertAlmostEqual(again['First mutant batch pays for cold lanes'],
                               by_name['First mutant batch pays for cold lanes'] + 100)
        self.assertAlmostEqual(again['Fresh-fork bootstrap on the critical path'],
                               by_name['Fresh-fork bootstrap on the critical path'])

    def test_extraction_needs_lane_evidence_at_a_matched_concurrency(self):
        args = type('Args', (), {})()
        for name in ('benchmark', 'benchmark_low', 'controller', 'profiles'):
            setattr(args, name, Path('/nonexistent'))
        with self.assertRaises(SystemExit):
            lanes.extract(args)


if __name__ == '__main__':
    unittest.main()
