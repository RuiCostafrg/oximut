"""The committed bottleneck report must be reproducible from its data.json."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'benchmarks/results/2026-11-07-bottleneck-report'


class BottleneckReport(unittest.TestCase):
    def test_render_reproduces_committed_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, '-I', str(ROOT / 'benchmarks/bottleneck_report.py'), 'render',
                            '--data', str(REPORT / 'data.json'), '--output-dir', directory], check=True)
            for name in ('index.html', 'timeline.html', 'cpu.html'):
                self.assertEqual((Path(directory) / name).read_text(), (REPORT / name).read_text(), name)

    def test_pages_cross_link_and_carry_tables(self):
        for name in ('index.html', 'timeline.html', 'cpu.html'):
            page = (REPORT / name).read_text()
            for target in ('index.html', 'timeline.html', 'cpu.html'):
                self.assertIn(f'href="{target}"', page)
            # Low-contrast palette slots require a table view for every chart.
            self.assertGreaterEqual(page.count('<table>'), page.count('<svg'), name)


if __name__ == '__main__':
    unittest.main()
