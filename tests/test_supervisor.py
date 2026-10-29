"""Cleanup must not trust an empty, non-atomic process-table snapshot."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'oximut_supervisor', REPO / 'packages/vitest-adapter/supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorCleanup(unittest.TestCase):
    def run_supervisor(self, directory, snapshots, waits, identities=None, signals=None):
        report = Path(directory) / 'cleanup.json'
        libc = Mock()
        libc.prctl.return_value = 0
        with (patch.object(supervisor.ctypes, 'CDLL', return_value=libc),
              patch.object(supervisor.signal, 'signal'),
              patch.object(supervisor.os, 'close'),
              patch.object(supervisor.os, 'waitpid', side_effect=waits) as wait,
              patch.object(supervisor.os, 'kill', new=signals if signals is not None else Mock()) as kill,
              patch.object(supervisor.subprocess, 'Popen', return_value=Mock(pid=123)),
              patch.object(supervisor, 'process_identity',
                           side_effect=identities or FileNotFoundError()),
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
                [(0, 0), (0, 0), (0, 0), (123, 0), (0, 0), (456, 9), ChildProcessError()],
                [FileNotFoundError(), (parent, 'start-456', 'S')])
            # Three intervening polls skipped active discovery; stop forces it.
            self.assertEqual(discovery.call_count, 6)
            self.assertEqual(result, 0)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            evidence = json.loads(report.read_text())
            self.assertIn(456, evidence['tracked'])
            self.assertIn(456, evidence['reaped'])
            self.assertTrue(evidence['allReaped'])

    def test_shutdown_rereads_identity_before_signalling(self):
        parent = supervisor.os.getpid()
        observed = {456: (parent, 'original', 'S')}
        for current in [(parent, 'original', 'S'), (parent, 'reused', 'S'),
                        FileNotFoundError(), ProcessLookupError(), PermissionError(),
                        OSError('unreadable'), ValueError('malformed'), IndexError('truncated')]:
            with self.subTest(current=current), tempfile.TemporaryDirectory() as directory:
                with patch.object(supervisor.time, 'monotonic', return_value=10.0):
                    result, _, _, kill = self.run_supervisor(
                        directory, [observed, observed, {}, {}, {}, {}],
                        [(123, 0), (0, 0), ChildProcessError()],
                        [FileNotFoundError(), current])
                self.assertEqual(result, 0)
                if current == (parent, 'original', 'S'):
                    kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
                else:
                    kill.assert_not_called()

    def test_discovery_failure_cannot_publish_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                self.run_supervisor(directory, PermissionError('proc unavailable'), [])
            self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_first_discovery_failure_still_cleans_seeded_controller(self):
        error = PermissionError('initial discovery failed')
        signals = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError) as raised:
                self.run_supervisor(directory, error, [(123, 9), ChildProcessError()],
                                    [(1, 'controller-start', 'S'), (1, 'controller-start', 'S')],
                                    signals)
            self.assertIs(raised.exception, error)
            signals.assert_called_once_with(123, supervisor.signal.SIGKILL)
            self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_shutdown_discovery_failure_cleans_verified_identities_and_reraises(self):
        parent = supervisor.os.getpid()
        observed = {456: (parent, 'known', 'S'),
                    457: (parent, 'old', 'S'), 458: (parent, 'unreadable', 'S')}
        error = PermissionError('shutdown discovery failed')
        signal_child = Mock()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor, 'cleanup_known', wraps=supervisor.cleanup_known) as cleanup:
                with self.assertRaises(PermissionError) as raised:
                    # Controller seed, then fresh identities during fallback cleanup.
                    identities = [FileNotFoundError(), (parent, 'known', 'S'),
                                  (parent, 'new-generation', 'S'), PermissionError()]
                    self.run_supervisor(directory, [observed, error],
                                        [(123, 0), (0, 0), (456, 9), ChildProcessError()], identities, signal_child)
                self.assertIs(raised.exception, error)
                signal_child.assert_called_once_with(456, supervisor.signal.SIGKILL)
                self.assertEqual(cleanup.call_args.args[0],
                                 {456: 'known', 457: 'old', 458: 'unreadable'})
            self.assertFalse((Path(directory) / 'cleanup.json').exists())


class KnownIdentityCleanup(unittest.TestCase):
    @unittest.skipUnless(hasattr(supervisor.os, 'uname') and supervisor.os.uname().sysname == 'Linux',
                         'requires Linux /proc and waitpid')
    def test_real_detached_child_is_signalled_and_reaped(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                 start_new_session=True)
        try:
            stamp = supervisor.process_identity(child.pid)[1]
            supervisor.cleanup_known({child.pid: stamp}, time.monotonic() + 2)
            self.assertFalse(Path(f'/proc/{child.pid}').exists())
        finally:
            # Avoid leaving a child behind even if the cleanup assertion fails.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)

    def test_only_fresh_matching_identity_is_signalled_and_children_are_reaped(self):
        with (patch.object(supervisor, 'process_identity', side_effect=[
                (1, 'known', 'S'), (1, 'new-generation', 'S'), PermissionError(),
                FileNotFoundError(), ValueError('bad stat'), IndexError('short stat')]),
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid', side_effect=[(456, 9), ChildProcessError()]) as wait,
              patch.object(supervisor.time, 'monotonic', return_value=0)):
            supervisor.cleanup_known({456: 'known', 457: 'old', 458: 'unreadable',
                                      459: 'gone', 460: 'bad', 461: 'short'}, 2)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            self.assertEqual(wait.call_count, 2)

    def test_live_unverifiable_child_cannot_make_fallback_wait_forever(self):
        with (patch.object(supervisor, 'process_identity', side_effect=PermissionError()),
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid', return_value=(0, 0)),
              patch.object(supervisor.time, 'monotonic', side_effect=[0, 0, 0, 2]),
              patch.object(supervisor.time, 'sleep') as sleep):
            supervisor.cleanup_known({456: 'unreadable'}, 2)
            kill.assert_not_called()
            sleep.assert_called_once_with(0.01)

    def test_reap_loop_also_observes_cleanup_deadline(self):
        with (patch.object(supervisor.os, 'waitpid', return_value=(456, 9)) as wait,
              patch.object(supervisor.time, 'monotonic', side_effect=[0, 2])):
            supervisor.cleanup_known({}, 2)
            wait.assert_called_once()

    def test_expired_budget_does_not_start_identity_or_reaping_work(self):
        with (patch.object(supervisor, 'process_identity') as identity,
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid') as wait,
              patch.object(supervisor.time, 'monotonic', return_value=2)):
            supervisor.cleanup_known({456: 'known'}, 2)
            identity.assert_not_called()
            kill.assert_not_called()
            wait.assert_not_called()

    def test_identity_pass_stops_when_budget_expires(self):
        with (patch.object(supervisor, 'process_identity', return_value=(1, 'known', 'S')) as identity,
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid') as wait,
              patch.object(supervisor.time, 'monotonic', side_effect=[0, 0, 2])):
            supervisor.cleanup_known({456: 'known', 457: 'known'}, 2)
            identity.assert_called_once_with(456)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            wait.assert_not_called()
