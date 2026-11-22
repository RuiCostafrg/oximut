"""Diagnostic attribution preserves overlap and incomplete-stage boundaries."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).resolve().parents[1] / 'results/2026-11-14-batch-gap/summarize.py'
spec = importlib.util.spec_from_file_location('batch_gap', PATH)
batch_gap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch_gap)


class BatchGap(unittest.TestCase):
    def test_clips_stages_to_gap_and_unions_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, events in {
                'worker': [(0, 'sample.begin'), (10, 'controller.reply'),
                           (15, 'sample.end'), (16, 'verification.begin'),
                           (25, 'verification.end'), (40, 'controller.send')],
                'scheduler': [(12, 'sample.begin'), (20, 'sample.end'),
                              (30, 'sample.begin'), (50, 'sample.end')],
            }.items():
                (root / f'{name}.jsonl').write_text('\n'.join(
                    json.dumps({'ns': t * 1_000_000, 'event': event}) for t, event in events))
            result = batch_gap.summarize(root)
        self.assertEqual(result['intervals'], 1)
        self.assertEqual(result['median_ms']['gap'], 30)
        self.assertEqual(result['median_ms']['sample'], 20)
        self.assertEqual(result['median_ms']['verification'], 9)
        self.assertIsNone(result['rows'][0]['request_handoff'])
