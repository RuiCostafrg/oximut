"""Cleanup must not trust an empty, non-atomic process-table snapshot."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'oximut_supervisor', REPO / 'packages/vitest-adapter/supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorCleanup(unittest.TestCase):
    def run_supervisor(self, directory, snapshots, waits):
        report = Path(directory) / 'cleanup.json'
        libc = Mock()
        libc.prctl.return_value = 0
        with (patch.object(supervisor.ctypes, 'CDLL', return_value=libc),
              patch.object(supervisor.signal, 'signal'),
              patch.object(supervisor.os, 'close'),
              patch.object(supervisor.os, 'waitpid', side_effect=waits) as wait,
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.subprocess, 'Popen', return_value=Mock(pid=123)),
              patch.object(supervisor, 'snapshot', side_effect=snapshots),
              patch.object(supervisor.time, 'sleep'),
              patch.object(supervisor.sys, 'argv', ['supervisor', 'node', 'worker', str(report)]),
              patch.dict(supervisor.os.environ, {'NODE_CHANNEL_FD': '99'})):
            result = supervisor.main()
        return result, report, wait, kill

    def test_empty_snapshot_does_not_authorize_cleanup_while_child_is_live(self):
        with tempfile.TemporaryDirectory() as directory:
            result, report, wait, kill = self.run_supervisor(
                directory, lambda: {},
                [(123, 0), (0, 0), (0, 0), ChildProcessError()])
            self.assertEqual(result, 0)
            self.assertEqual(wait.call_count, 4)
            kill.assert_not_called()
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence['reaped'], [123])
            self.assertTrue(evidence['allReaped'])

    def test_newly_adopted_child_is_discovered_before_signalling(self):
        parent = supervisor.os.getpid()
        adopted = {456: (parent, 'start-456', 'S')}
        # Empty active scan, adoption discovered only after controller reaping.
        tables = iter([{}, adopted, adopted, {}, {}, {}])
        discovery = Mock(side_effect=lambda: next(tables))
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(supervisor.time, 'monotonic', return_value=10.0)):
            result, report, _, kill = self.run_supervisor(
                directory, discovery,
                [(0, 0), (0, 0), (0, 0), (123, 0), (0, 0), (456, 9), ChildProcessError()])
            # Three intervening polls skipped active discovery; stop forces it.
            self.assertEqual(discovery.call_count, 6)
            self.assertEqual(result, 0)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            evidence = json.loads(report.read_text())
            self.assertIn(456, evidence['tracked'])
            self.assertIn(456, evidence['reaped'])
            self.assertTrue(evidence['allReaped'])

    def test_discovery_failure_cannot_publish_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                self.run_supervisor(directory, PermissionError('proc unavailable'), [])
            self.assertFalse((Path(directory) / 'cleanup.json').exists())
