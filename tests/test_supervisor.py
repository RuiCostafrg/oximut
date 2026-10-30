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


class PidfdRoutedToKill(unittest.TestCase):
    """Route pidfd signalling through os.kill mocks; fd numbers mirror PIDs."""

    def setUp(self):
        for name, replacement in [
                ('pidfd_open', lambda pid: pid),
                ('pidfd_kill', lambda fd: supervisor.os.kill(fd, supervisor.signal.SIGKILL))]:
            patcher = patch.object(supervisor, name, side_effect=replacement)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(supervisor.os, 'close')
        self.close = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(supervisor, 'probe_pidfd')
        self.probe = patcher.start()
        self.addCleanup(patcher.stop)


class SupervisorCleanup(PidfdRoutedToKill):
    def run_supervisor(self, directory, snapshots, waits, identities=None, signals=None, handlers=None,
                       controller=None):
        report = Path(directory) / 'cleanup.json'
        libc = Mock()
        libc.prctl.return_value = 0
        with (patch.object(supervisor.ctypes, 'CDLL', return_value=libc),
              patch.object(supervisor.signal, 'signal',
                           side_effect=lambda number, handler:
                           handlers.update({number: handler}) if handlers is not None else None),
              patch.object(supervisor.os, 'close'),
              patch.object(supervisor.os, 'waitpid', side_effect=waits) as wait,
              patch.object(supervisor.os, 'kill', new=signals if signals is not None else Mock()) as kill,
              patch.object(supervisor.subprocess, 'Popen', return_value=controller or Mock(pid=123)),
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
                directory, lambda _deadline=None: {},
                [(123, 0), (0, 0), (0, 0), ChildProcessError(), ChildProcessError()])
            self.assertEqual(result, 0)
            # ECHILD is rechecked after the final empty scan before success.
            self.assertEqual(wait.call_count, 5)
            kill.assert_not_called()
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence['reaped'], [123])
            self.assertTrue(evidence['allReaped'])

    def test_newly_adopted_child_is_discovered_before_signalling(self):
        parent = supervisor.os.getpid()
        adopted = {456: (parent, 'start-456', 'S')}
        # Empty active scan, adoption discovered only after controller reaping.
        tables = iter([{}, adopted, adopted, {}, {}, {}])
        discovery = Mock(side_effect=lambda _deadline=None: next(tables))
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(supervisor.time, 'monotonic', return_value=10.0)):
            result, report, _, kill = self.run_supervisor(
                directory, discovery,
                [(0, 0), (0, 0), (0, 0), (123, 0), (0, 0), (456, 9), ChildProcessError(),
                 ChildProcessError()],
                [FileNotFoundError(), (parent, 'start-456', 'S')])
            # Three intervening polls skipped active discovery; stop forces it.
            self.assertEqual(discovery.call_count, 5)
            self.assertEqual(result, 0)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            evidence = json.loads(report.read_text())
            self.assertIn(456, evidence['tracked'])
            self.assertIn(456, evidence['reaped'])
            self.assertTrue(evidence['allReaped'])

    def test_shutdown_snapshot_overrun_cannot_publish_empty_tree_success(self):
        for slow_scan in [2, 3]:
            now = [10.0]
            calls = []

            def discovery(deadline=None):
                calls.append(deadline)
                if len(calls) == slow_scan:
                    self.assertEqual(deadline, 12.0)
                    now[0] = 12.1
                return {}

            with self.subTest(slow_scan=slow_scan), tempfile.TemporaryDirectory() as directory:
                with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                    with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                        self.run_supervisor(directory, discovery, [(123, 0), ChildProcessError()])
                self.assertEqual(len(calls), slow_scan)
                self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_signal_during_active_scan_starts_budget_before_shutdown_scan(self):
        now = [10.0]
        handlers = {}
        calls = []

        def discovery(deadline=None):
            calls.append(deadline)
            if len(calls) == 1:
                supervisor.check_deadline(deadline)
                handlers[supervisor.signal.SIGTERM](None, None)
                now[0] = 11.0
                supervisor.check_deadline(deadline)
            else:
                self.assertEqual(deadline, 12.0)
                # The signal's original budget must not restart at shutdown.
                now[0] = 12.1
            return {}

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                    self.run_supervisor(directory, discovery, [(0, 0)], handlers=handlers)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], 12.0)
            self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_active_scan_enforces_budget_started_mid_enumeration(self):
        now = [10.0]
        handlers = {}
        calls = []

        def discovery(deadline=None):
            calls.append(deadline)
            handlers[supervisor.signal.SIGTERM](None, None)
            now[0] = 12.1
            # Remaining enumeration must see the new budget, not a cached None.
            supervisor.check_deadline(deadline)
            self.fail('scan continued past the signal-started cleanup budget')

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                    self.run_supervisor(directory, discovery, [], handlers=handlers)
            self.assertEqual(len(calls), 1)
            self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_snapshot_checks_budget_after_identity_read(self):
        now = [10.0]

        def identity(_pid):
            now[0] = 12.1
            return (1, 'start', 'S')

        with (patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]),
              patch.object(supervisor.os, 'listdir', return_value=['123', '456']),
              patch.object(supervisor, 'process_identity', side_effect=identity) as read):
            with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                supervisor.snapshot(12.0)
            self.assertEqual(read.call_count, 1)

    def test_descendant_traversal_checks_budget_during_iteration(self):
        class SlowTable(dict):
            def items(self):
                yield 123, (1, 'start', 'S')
                now[0] = 12.1
                yield 456, (123, 'start', 'S')

        now = [10.0]
        with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
            with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                supervisor.descendants(SlowTable(), 1, 12.0)

    def test_shutdown_identity_overrun_does_not_signal(self):
        parent = supervisor.os.getpid()
        observed = {456: (parent, 'start', 'S')}
        now = [10.0]
        reads = []

        def identity(pid):
            reads.append(pid)
            if pid == 123:
                raise FileNotFoundError()
            now[0] = 12.1
            return (parent, 'start', 'S')

        with tempfile.TemporaryDirectory() as directory:
            signals = Mock()
            with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                    self.run_supervisor(directory, [observed, observed],
                                        [(123, 0), (0, 0)], identity, signals)
            signals.assert_not_called()
            self.assertEqual(reads, [123, 456])
            self.assertFalse((Path(directory) / 'cleanup.json').exists())

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
                        [(123, 0), (0, 0), ChildProcessError(), ChildProcessError()],
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

    def test_initial_identity_failure_kills_and_waits_for_direct_child_with_shared_budget(self):
        for error in [PermissionError('denied'), ValueError('malformed'), IndexError('truncated')]:
            now = [10.0]
            controller = Mock(pid=123)
            controller.kill.side_effect = lambda: now.__setitem__(0, 11.0)
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                with (patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]),
                      patch.object(supervisor, 'cleanup_known') as cleanup):
                    with self.assertRaises(type(error)) as raised:
                        self.run_supervisor(directory, [], [], error, controller=controller)
                self.assertIs(raised.exception, error)
                controller.kill.assert_called_once_with()
                controller.wait.assert_called_once_with(timeout=1.0)
                cleanup.assert_called_once_with({}, 12.0)
                self.assertFalse((Path(directory) / 'cleanup.json.ready').exists())
                self.assertFalse((Path(directory) / 'cleanup.json').exists())

    def test_initial_identity_failure_cannot_restart_expired_signal_budget(self):
        now = [10.0]
        handlers = {}
        controller = Mock(pid=123)
        error = PermissionError('initial identity denied')

        def identity(_pid):
            handlers[supervisor.signal.SIGTERM](None, None)
            now[0] = 12.1
            raise error

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                with self.assertRaises(PermissionError) as raised:
                    self.run_supervisor(directory, [], [], identity, handlers=handlers,
                                        controller=controller)
            self.assertIs(raised.exception, error)
            controller.kill.assert_not_called()
            controller.wait.assert_not_called()
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


    def test_empty_final_scan_rechecks_waitpid_before_success(self):
        parent = supervisor.os.getpid()
        adopted = {456: (parent, 'start-456', 'S')}
        # ECHILD, then an empty final scan races with an adoption: waitpid shows
        # a live child, so discovery repeats, signals it and reaps it.
        tables = iter([{}, {}, {}, adopted, adopted, {}, {}])
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor.time, 'monotonic', return_value=10.0):
                result, report, wait, kill = self.run_supervisor(
                    directory, lambda _deadline=None: next(tables),
                    [(123, 0), ChildProcessError(), (0, 0), (456, 9), ChildProcessError(),
                     ChildProcessError()],
                    [FileNotFoundError(), (parent, 'start-456', 'S')])
            self.assertEqual(result, 0)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            evidence = json.loads(report.read_text())
            self.assertEqual(evidence['reaped'], [123, 456])
            self.assertTrue(evidence['allReaped'])

    def test_missing_pidfd_support_fails_closed_without_numeric_kill(self):
        parent = supervisor.os.getpid()
        observed = {456: (parent, 'start', 'S')}
        now = [10.0]
        signals = Mock()

        def unavailable(_pid):
            # The startup probe passes; the descendant path loses pidfd support.
            now[0] = 12.1
            raise OSError(38, 'Function not implemented')

        self.pidfd_open.side_effect = unavailable
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]):
                with self.assertRaisesRegex(RuntimeError, 'cleanup bound'):
                    self.run_supervisor(directory, lambda _deadline=None: observed,
                                        [(123, 0), ChildProcessError()],
                                        lambda _pid: (parent, 'start', 'S'), signals)
            self.assertFalse((Path(directory) / 'cleanup.json').exists())
        signals.assert_not_called()
        self.pidfd_kill.assert_not_called()

    def test_unavailable_pidfd_aborts_before_spawning(self):
        self.probe.side_effect = OSError(38, 'Function not implemented')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(supervisor.subprocess, 'Popen') as popen:
            with self.assertRaises(OSError):
                self.run_supervisor(directory, [], [])
            popen.assert_not_called()

    def test_discovery_failure_leaves_causal_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                self.run_supervisor(directory, PermissionError('proc unavailable'), [])
            diagnostic = json.loads((Path(directory) / 'cleanup.json.error').read_text())
            self.assertEqual(diagnostic['error'], 'PermissionError: proc unavailable')
            self.assertFalse((Path(directory) / 'cleanup.json').exists())


class PidfdProbe(unittest.TestCase):
    def test_probe_exercises_open_and_send_and_closes(self):
        for failing in [None, 'pidfd_open', 'pidfd_signal']:
            with self.subTest(failing=failing), \
                    patch.object(supervisor, 'pidfd_open', return_value=77) as opened, \
                    patch.object(supervisor, 'pidfd_signal') as sent, \
                    patch.object(supervisor.os, 'close') as close:
                denied = PermissionError('denied by seccomp')
                if failing:
                    {'pidfd_open': opened, 'pidfd_signal': sent}[failing].side_effect = denied
                    with self.assertRaises(PermissionError):
                        supervisor.probe_pidfd()
                else:
                    supervisor.probe_pidfd()
                opened.assert_called_once_with(supervisor.os.getpid())
                if failing == 'pidfd_open':
                    sent.assert_not_called()
                    close.assert_not_called()
                else:
                    sent.assert_called_once_with(77, 0)
                    close.assert_called_once_with(77)


class SignalVerified(PidfdRoutedToKill):
    def test_pidfd_is_pinned_before_identity_reread_and_always_closed(self):
        order = []
        self.pidfd_open.side_effect = lambda pid: order.append('open') or 77
        self.pidfd_kill.side_effect = lambda fd: order.append(('kill', fd))
        self.close.side_effect = lambda fd: order.append(('close', fd))
        for identity, signalled in [((1, 'known', 'S'), True), ((1, 'reused', 'S'), False)]:
            order.clear()
            with self.subTest(identity=identity), \
                    patch.object(supervisor, 'process_identity',
                                 side_effect=lambda _pid, value=identity: order.append('read') or value):
                self.assertIs(supervisor.signal_verified(456, 'known', None), signalled)
                expected = ['open', 'read'] + ([('kill', 77)] if signalled else []) + [('close', 77)]
                self.assertEqual(order, expected)
        order.clear()
        with patch.object(supervisor, 'process_identity', side_effect=PermissionError()):
            with self.assertRaises(PermissionError):
                supervisor.signal_verified(456, 'known', None)
        self.assertEqual(order, ['open', ('close', 77)])

    def test_exited_pinned_generation_is_not_an_error(self):
        self.pidfd_kill.side_effect = ProcessLookupError()
        with patch.object(supervisor, 'process_identity', return_value=(1, 'known', 'S')):
            self.assertFalse(supervisor.signal_verified(456, 'known', None))
        self.close.assert_called_once_with(456)

    def test_deadline_after_reread_does_not_signal(self):
        now = [10.0]
        with (patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]),
              patch.object(supervisor, 'process_identity',
                           side_effect=lambda _pid: now.__setitem__(0, 12.1) or (1, 'known', 'S'))):
            with self.assertRaises(supervisor.CleanupDeadline):
                supervisor.signal_verified(456, 'known', 12.0)
        self.pidfd_kill.assert_not_called()
        self.close.assert_called_once_with(456)


class KnownIdentityCleanup(PidfdRoutedToKill):
    def test_identity_read_overrun_cannot_signal_or_reap(self):
        now = [10.0]

        def identity(_pid):
            now[0] = 12.1
            return (1, 'start', 'S')

        with (patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]),
              patch.object(supervisor, 'process_identity', side_effect=identity),
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid') as wait):
            supervisor.cleanup_known({123: 'start'}, 12.0)
            kill.assert_not_called()
            wait.assert_not_called()

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
        now = [0]
        with (patch.object(supervisor, 'process_identity', side_effect=PermissionError()),
              patch.object(supervisor.os, 'kill') as kill,
              patch.object(supervisor.os, 'waitpid', return_value=(0, 0)),
              patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]),
              patch.object(supervisor.time, 'sleep',
                           side_effect=lambda _seconds: now.__setitem__(0, 2)) as sleep):
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
        now = [0]
        with (patch.object(supervisor, 'process_identity', return_value=(1, 'known', 'S')) as identity,
              patch.object(supervisor.os, 'kill',
                           side_effect=lambda *_args: now.__setitem__(0, 2)) as kill,
              patch.object(supervisor.os, 'waitpid') as wait,
              patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0])):
            supervisor.cleanup_known({456: 'known', 457: 'known'}, 2)
            identity.assert_called_once_with(456)
            kill.assert_called_once_with(456, supervisor.signal.SIGKILL)
            wait.assert_not_called()


class RealPidfdCleanup(unittest.TestCase):
    """No mocks: the identity-bound signal path must work on this kernel."""

    def setUp(self):
        # Probe before spawning so an unsupported system skips without children.
        try:
            supervisor.probe_pidfd()
        except (AttributeError, OSError) as error:
            self.skipTest(f'pidfds unavailable: {error}')

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

    @unittest.skipUnless(hasattr(supervisor.os, 'uname') and supervisor.os.uname().sysname == 'Linux',
                         'requires Linux pidfds')
    def test_pidfd_of_reaped_generation_cannot_signal_its_pid(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        fd = None
        try:
            fd = supervisor.pidfd_open(child.pid)
            child.kill()
            child.wait(timeout=3)
            # The numeric PID is now free for reuse; the handle still names the
            # reaped generation, so it can never reach a replacement process.
            with self.assertRaises(ProcessLookupError):
                supervisor.pidfd_kill(fd)
        finally:
            if fd is not None:
                supervisor.os.close(fd)
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
