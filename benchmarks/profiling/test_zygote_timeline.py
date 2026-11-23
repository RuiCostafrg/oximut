"""Synthetic timeline traces only; no builds, dependencies or performance claims."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'benchmarks/profiling'))
import zygote_timeline as timeline


def event(files, role, pid, t, name, **fields):
    files.setdefault(f'{role}-{pid}.jsonl', []).append(
        {'t': t, 'event': name, 'role': role, 'pid': pid, **fields})


def batch(files, shift, job, lanes):
    """One two-lane batch on slot 0. `lanes` maps mutant id to its worker pid and result delay."""
    s = lambda offset: 1000 + shift + offset
    ids = list(lanes)
    event(files, 'scheduler', 1, s(0), 'dispatch_begin', slot=0, job=job, kind='mutant', mutant=ids[0], lanes=ids)
    event(files, 'scheduler', 1, s(2), 'dispatch_sent', slot=0, job=job, kind='mutant', mutant=ids[0], lanes=ids)
    event(files, 'controller', 2, s(3), 'request_received', id=job, command='run', lanes=ids)
    event(files, 'controller', 2, s(4), 'batch_enter', lanes=ids)
    event(files, 'controller', 2, s(10), 'context_ready')
    event(files, 'controller', 2, s(12), 'pool_run_begin')
    for mutant, (pid, delay) in lanes.items():
        event(files, 'controller', 2, s(12), 'lane_fork_requested', lane='x', mutant=mutant)
        event(files, 'pool', 2, s(12), 'spawn_requested', id=pid, mutant=mutant)
        event(files, 'pool', 2, s(13), 'spawn_sent', id=pid, mutant=mutant)
        event(files, 'zygote', 3, s(14), 'spawn_received', id=pid, mutant=mutant)
        event(files, 'zygote', 3, s(15), 'fork_begin', id=pid, mutant=mutant)
        event(files, 'zygote', 3, s(19), 'forked', id=pid, workerPid=pid, mutant=mutant)
        event(files, 'worker', pid, s(20), 'fork_entered', id=pid, mutant=mutant)
        event(files, 'pool', 2, s(21), 'spawned', id=pid, mutant=mutant, workerPid=pid)
        event(files, 'controller', 2, s(21), 'lane_fork_returned', lane='x', mutant=mutant)
        event(files, 'controller', 2, s(22), 'lane_start_sent', lane='x', mutant=mutant)
        event(files, 'worker', pid, s(23), 'first_message', id=pid)
        event(files, 'controller', 2, s(60), 'lane_started', lane='x', mutant=mutant)
        event(files, 'controller', 2, s(61), 'lane_run_sent', lane='x', mutant=mutant)
        event(files, 'worker', pid, s(62), 'collect_begin', mutant=mutant)
        event(files, 'worker', pid, s(100), 'collect_end', mutant=mutant)
        event(files, 'worker', pid, s(100), 'execute_begin', mutant=mutant)
        event(files, 'worker', pid, s(120), 'execute_end', mutant=mutant)
        event(files, 'controller', 2, s(125 + delay), 'lane_result', lane='x', mutant=mutant)
        event(files, 'controller', 2, s(130 + delay), 'lane_exit', lane='x', mutant=mutant)
    event(files, 'controller', 2, s(160), 'pool_run_end')
    event(files, 'controller', 2, s(165), 'pool_closed')
    event(files, 'controller', 2, s(167), 'reply_sent', id=job, command='run')
    event(files, 'scheduler', 1, s(169), 'reply', slot=0, job=job, kind='mutant', mutant=ids[0], lanes=ids, ok=True)


def write(directory, files):
    for name, items in files.items():
        (Path(directory) / name).write_text(''.join(json.dumps(item) + '\n' for item in items))


class ZygoteTimelineTests(unittest.TestCase):
    def synthetic(self):
        files = {}
        event(files, 'scheduler', 1, 500, 'cli_start')
        event(files, 'scheduler', 1, 600, 'dispatch_begin', slot=0, job=0, kind='baseline')
        event(files, 'scheduler', 1, 700, 'reply', slot=0, job=0, kind='baseline', ok=True)
        event(files, 'pool', 2, 990, 'zygote_start')
        event(files, 'pool', 2, 995, 'zygote_ready')
        batch(files, 0, 1, {'a': (11, 0), 'b': (12, 30)})
        batch(files, 180, 2, {'c': (13, 0), 'd': (14, 30)})
        return files

    def test_summary_reports_each_metric_of_a_two_batch_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, self.synthetic())
            (Path(directory) / 'broken.jsonl').write_text('not json\n{"t": 1}\n')
            summary = timeline.analyze(directory)
        per = summary['per_mutant_ms']
        self.assertEqual(summary['malformed_lines'], 2)
        self.assertEqual(per['fork_latency']['median'], 9)
        self.assertEqual(per['fork_syscall']['median'], 4)
        self.assertEqual(per['start_to_run']['median'], 39)
        self.assertEqual(per['run_to_result']['median'], (64 + 94) / 2)
        self.assertEqual(per['collect']['median'], 38)
        self.assertEqual(per['execute']['median'], 20)
        self.assertEqual(per['result_to_reply']['median'], (42 + 12) / 2)
        self.assertEqual(per['run_to_result']['n'], 4)
        self.assertFalse(any(summary['negative_segment_samples'].values()))
        # The slot hands over 11 ms after the first reply; the batch tail is 30 ms.
        self.assertEqual(summary['scheduler_ms']['reply_to_next_dispatch']['median'], 11)
        self.assertEqual(summary['scheduler_ms']['dispatch_to_request_received']['median'], 1)
        self.assertEqual(summary['controller_batch_ms']['pool_run']['median'], 148)
        self.assertEqual(summary['lane_tail_skew_ms']['median'], 30)
        self.assertEqual(summary['startup_ms']['cli_start_to_first_mutant_dispatch'], 500)
        self.assertEqual(summary['zygote_startup_ms']['start_to_ready'], 5)
        slot = summary['mutation_phase']['slots']['0']
        self.assertEqual(summary['mutation_phase']['mutants'], 4)
        self.assertAlmostEqual(slot['busy_fraction'], 338 / 349)
        self.assertAlmostEqual(slot['busy_fraction'] + slot['idle_fraction'], 1)
        self.assertEqual(summary['lane_occupancy']['width'], 2)

    def test_cli_writes_json_and_text(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, self.synthetic())
            output = Path(directory) / 'summary.json'
            self.assertEqual(timeline.main([directory, '--json', str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())['events'], sum(map(len, self.synthetic().values())))
            self.assertIn('lane batch tail skew', timeline.render(json.loads(output.read_text())))

    def test_empty_directory_fails_instead_of_reporting_zeros(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(timeline.main([directory]), 1)


if __name__ == '__main__':
    unittest.main()
