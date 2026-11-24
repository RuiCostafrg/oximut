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


def batch(files, shift, job, lanes, elu=((0, 0), (0, 0)), template=False):
    """One two-lane batch on slot 0. `lanes` maps mutant id to its worker pid and result delay.

    `elu` is the cumulative controller (active, idle) ms at batch_enter and batch_done;
    `template` adds a template build and forks the lanes from it (precollect)."""
    s = lambda offset: 1000 + shift + offset
    ids = list(lanes)
    event(files, 'scheduler', 1, s(0), 'dispatch_begin', slot=0, job=job, kind='mutant', mutant=ids[0], lanes=ids)
    event(files, 'scheduler', 1, s(2), 'dispatch_sent', slot=0, job=job, kind='mutant', mutant=ids[0], lanes=ids)
    event(files, 'controller', 2, s(3), 'request_received', id=job, command='run', lanes=ids)
    event(files, 'controller', 2, s(4), 'batch_enter', lanes=ids, eluActive=elu[0][0], eluIdle=elu[0][1])
    event(files, 'controller', 2, s(10), 'context_ready')
    if template:
        event(files, 'controller', 2, s(10), 'template_build_begin', key='["t"]')
        event(files, 'controller', 2, s(11), 'template_build_end', key='["t"]', ready=True, ms=40.5)
    event(files, 'controller', 2, s(12), 'pool_run_begin')
    for mutant, (pid, delay) in lanes.items():
        event(files, 'controller', 2, s(12), 'lane_fork_requested', lane='x', mutant=mutant)
        event(files, 'pool', 2, s(12), 'spawn_requested', id=pid, mutant=mutant)
        if template:
            event(files, 'pool', 2, s(12), 'template_fork_requested', id=pid, mutant=mutant)
            event(files, 'pool', 2, s(21), 'template_fork_attached', id=pid, mutant=mutant)
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
    event(files, 'controller', 2, s(166), 'batch_done', eluActive=elu[1][0], eluIdle=elu[1][1])
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
        batch(files, 0, 1, {'a': (11, 0), 'b': (12, 30)}, elu=((10, 10), (50, 60)))
        batch(files, 180, 2, {'c': (13, 0), 'd': (14, 30)}, elu=((60, 70), (110, 110)), template=True)
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

    def test_event_loop_utilization_differences_each_controller_separately(self):
        files = self.synthetic()
        # A second controller (another slot) whose cumulative counters are unrelated to the first's:
        # differencing across processes would give a negative idle and >100% busy.
        event(files, 'controller', 9, 1100, 'batch_enter', eluActive=5000, eluIdle=9000)
        event(files, 'controller', 9, 1300, 'batch_done', eluActive=5300, eluIdle=9100)
        with tempfile.TemporaryDirectory() as directory:
            write(directory, files)
            loop = timeline.analyze(directory)['controller_event_loop']
        self.assertEqual({c['pid']: (c['active_ms'], c['idle_ms']) for c in loop['controllers']},
                         {2: (100, 100), 9: (300, 100)})
        self.assertAlmostEqual(loop['utilization'], 400 / 600)
        self.assertLessEqual(loop['utilization'], 1)

    def test_precollect_template_builds_event_loop_and_pool_run_breakdown(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, self.synthetic())
            summary = timeline.analyze(directory)
            text = timeline.render(summary)
        self.assertEqual(summary['template_builds']['count'], 1)
        self.assertEqual(summary['template_builds']['total_ms'], 40.5)
        # Cumulative samples 10/10 at the first batch_enter, 110/110 at the last batch_done.
        self.assertAlmostEqual(summary['controller_event_loop']['utilization'], 0.5)
        pre = summary['precollect_per_mutant_ms']
        self.assertEqual(pre['templated_mutants'], 2)
        self.assertEqual(pre['templated_fork_to_attach']['median'], 9)
        self.assertEqual(pre['templated_start_to_collect_end']['median'], 78)
        # In each batch lane 1 finishes 30 ms before lane 2; the run ends 5 ms after its last result.
        pool = summary['pool_run_breakdown_ms']
        self.assertEqual(pool['batches'], 2)
        self.assertEqual(pool['pool_run']['median'], 148)
        self.assertEqual(pool['slowest_lane']['median'], 143)
        self.assertEqual(pool['after_slowest_lane']['median'], 5)
        self.assertEqual(pool['lane_wait_for_slowest_mean']['median'], 15)
        self.assertEqual(pool['lane_wait_for_slowest_max']['median'], 30)
        self.assertAlmostEqual(pool['lane_wait_fraction']['mean'], 30 / (148 * 2))
        for line in ('template builds: 1', 'controller event loop', 'pool_run per batch (2 batches', 'precollect, templated'):
            self.assertIn(line, text)

    def rolling_trace(self):
        """One rolling batch of five lanes through two slots: slot 0 runs m0, m2, m4 back to
        back with 5 ms between jobs, slot 1 runs m1 for 300 ms and then m3."""
        files = {}
        ids = ['m0', 'm1', 'm2', 'm3', 'm4']
        event(files, 'scheduler', 1, 500, 'cli_start')
        event(files, 'scheduler', 1, 1000, 'dispatch_begin', slot=0, job=1, kind='mutant', mutant='m0', lanes=ids)
        event(files, 'scheduler', 1, 1002, 'dispatch_sent', slot=0, job=1, kind='mutant', mutant='m0', lanes=ids)
        event(files, 'controller', 2, 1003, 'context_created', width=2)
        event(files, 'controller', 2, 1004, 'rolling_begin', lanes=5, slots=2)
        # (lane, begin, exit) of each lane job; the worker lives from fork request to exit.
        jobs = {'m0': ('oximut-lane-0', 1010, 1100), 'm1': ('oximut-lane-1', 1010, 1310),
                'm2': ('oximut-lane-0', 1105, 1205), 'm3': ('oximut-lane-1', 1315, 1365),
                'm4': ('oximut-lane-0', 1210, 1260)}
        for mutant, (lane, begin, exit_) in jobs.items():
            event(files, 'controller', 2, begin, 'lane_job_begin', lane=lane, mutant=mutant)
            event(files, 'controller', 2, begin + 1, 'lane_fork_requested', lane=lane, mutant=mutant)
            event(files, 'controller', 2, begin + 20, 'lane_run_sent', lane=lane, mutant=mutant)
            event(files, 'controller', 2, exit_ - 4, 'lane_result', lane=lane, mutant=mutant)
            event(files, 'controller', 2, exit_ - 1, 'lane_exit', lane=lane, mutant=mutant)
            event(files, 'controller', 2, exit_, 'lane_job_end', lane=lane, mutant=mutant)
        event(files, 'controller', 2, 1370, 'rolling_end')
        event(files, 'controller', 2, 1371, 'reply_sent', id=1, command='run')
        event(files, 'scheduler', 1, 1373, 'reply', slot=0, job=1, kind='mutant', mutant='m0', lanes=ids, ok=True)
        return files

    def test_rolling_batch_reports_lane_slots_refill_gaps_and_occupancy_over_time(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, self.rolling_trace())
            summary = timeline.analyze(directory)
            text = timeline.render(summary)
        # The lane slots come from the controller, not from the five lanes of the batch.
        self.assertEqual(summary['lane_occupancy']['width'], 2)
        rolling = summary['rolling']
        self.assertEqual(rolling['batches'], 1)
        self.assertEqual(rolling['slots'], 2)
        self.assertEqual(rolling['lanes_per_batch']['median'], 5)
        # Slot 0 refills after 5 ms twice; slot 1 after 5 ms once.
        self.assertEqual(rolling['refill_gap_ms']['n'], 3)
        self.assertEqual(rolling['refill_gap_ms']['median'], 5)
        self.assertEqual(rolling['lane_job_ms']['n'], 5)
        self.assertEqual(rolling['lane_job_ms']['max'], 300)
        # No barrier tail is reported for a batch that is not one.
        self.assertIsNone(summary['lane_tail_skew_ms'])
        occupancy = summary['lane_occupancy_over_time']
        self.assertEqual(occupancy['width'], 2)
        # Phase 1000..1373. No worker is alive for 11 ms at the start, 7 ms while slot 1
        # refills after m1 and 9 ms after the last exit; slot 1 runs alone from 1259 to 1309.
        self.assertGreater(occupancy['alive_mean'], 1.5)
        self.assertLess(occupancy['time_below_width_fraction'], 0.4)
        self.assertAlmostEqual(occupancy['time_with_no_worker_fraction'], 27 / 373)
        self.assertEqual(len(occupancy['slices']), 10)
        for line in ('rolling lanes: 1 batches over 2 slots', 'slot refill gap', 'lane workers alive over time'):
            self.assertIn(line, text)

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
