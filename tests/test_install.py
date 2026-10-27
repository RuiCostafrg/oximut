"""Cheap source installer and launcher checks; full installation smoke is scripts/smoke-install.py."""
import importlib.util
import json
import argparse
import errno
import sys
import time
import subprocess
import tempfile
from unittest import mock
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "oximut_launcher", Path(__file__).resolve().parents[1] / "scripts" / "oximut-launcher.py")
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


INSTALL_SPEC = importlib.util.spec_from_file_location(
    "oximut_installer", Path(__file__).resolve().parents[1] / "scripts" / "install-source.py")
INSTALLER = importlib.util.module_from_spec(INSTALL_SPEC)
INSTALL_SPEC.loader.exec_module(INSTALLER)


class InstalledLauncher(unittest.TestCase):
    def test_prefix_and_mode_resolution(self):
        prefix = Path("/tmp/relocated prefix")
        for arguments, runner in [([], "m1"), (["--execution", "persistent"], "m2"),
                                  (["--execution", "persistent", "--execution", "fresh"], "m1")]:
            self.assertEqual(LAUNCHER.command(prefix, arguments), [
                str(prefix / "lib/oximut/oximut"), "--adapter",
                str(prefix / f"lib/oximut/packages/vitest-adapter/{runner}-runner.mjs"), *arguments])

    def test_explicit_adapter_and_option_values(self):
        prefix = Path("/tmp/installation")
        arguments = ["--execution", "persistent", "--adapter", "custom runner.mjs"]
        self.assertEqual(LAUNCHER.command(prefix, arguments),
                         [str(prefix / "lib/oximut/oximut"), *arguments])
        # Values that happen to look like flags must not change launcher mode.
        arguments = ["--config", "--execution", "--source", "persistent"]
        self.assertIn("m1-runner.mjs", " ".join(LAUNCHER.command(prefix, arguments)))


class SourceInstaller(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "packages/vitest-adapter").mkdir(parents=True)
        for name in ("package.json", "package-lock.json", ".npmrc", "LICENSE",
                     "scripts/oximut-launcher.py"):
            (self.repo / name).write_text("fixture")
        self.prefix = Path(self.temporary.name) / "installed"
        self.stale = self.repo / "target/release/oximut"
        self.stale.parent.mkdir(parents=True)
        self.stale.write_text("stale binary")
        self.patch_file = mock.patch.object(INSTALLER, "__file__", str(self.repo / "scripts/install-source.py"))
        self.patch_file.start()
        self.addCleanup(self.patch_file.stop)

    def artifact(self, executable, **target):
        return json.dumps({"reason": "compiler-artifact", "executable": str(executable),
                           "target": {"name": "oximut", "kind": ["bin"], **target}})

    def test_installs_current_reported_configured_target_artifact(self):
        executable = self.repo / "target/aarch64-unknown-linux-gnu/release/oximut"
        executable.parent.mkdir(parents=True)
        executable.write_text("current configured-target binary")
        # Cargo reports fresh artifacts too; unrelated targets must be ignored.
        output = self.artifact(self.stale, name="other") + "\n" + self.artifact(executable)
        with mock.patch.object(INSTALLER, "run_phase") as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, output),
                               subprocess.CompletedProcess([], 0)]
            INSTALLER.install(self.prefix, build_timeout_seconds=123, npm_timeout_seconds=45)
        self.assertEqual(run.call_args_list[0].kwargs["timeout_seconds"], 123)
        self.assertEqual(run.call_args_list[1].kwargs["timeout_seconds"], 45)
        self.assertEqual((self.prefix / "lib/oximut/oximut").read_text(), executable.read_text())
        command = run.call_args_list[0].args[0]
        for argument in ("--locked", "--release", "--message-format=json-render-diagnostics"):
            self.assertIn(argument, command)

    def test_missing_artifact_never_falls_back_to_stale_binary(self):
        with mock.patch.object(INSTALLER, "run_phase", return_value=
                               subprocess.CompletedProcess([], 0, "")) as run:
            with self.assertRaisesRegex(SystemExit, "exactly one"):
                INSTALLER.install(self.prefix)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.prefix / "bin/oximut").exists())
        self.assertFalse((self.prefix / "lib/oximut").exists())

    def test_invalid_artifact_report_does_not_install(self):
        missing = self.repo / "target/configured/release/oximut"
        for output, error in [
                (self.artifact(missing), "does not exist"),
                (self.artifact(self.stale) + "\n" + self.artifact(missing), "exactly one")]:
            with self.subTest(error=error):
                with mock.patch.object(INSTALLER, "run_phase", return_value=
                                       subprocess.CompletedProcess([], 0, output)) as run:
                    with self.assertRaisesRegex(SystemExit, error):
                        INSTALLER.install(self.prefix)
                self.assertEqual(run.call_count, 1)
                self.assertFalse((self.prefix / "lib/oximut").exists())

    def test_failed_build_never_installs_reported_artifact(self):
        with mock.patch.object(INSTALLER, "run_phase", return_value=
                               subprocess.CompletedProcess([], 1, self.artifact(self.stale))) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                INSTALLER.install(self.prefix)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.prefix / "lib/oximut").exists())


    def test_invalid_deadlines_have_no_prefix_side_effects(self):
        for option in ("build_timeout_seconds", "npm_timeout_seconds"):
            for value in (0, -1, float("nan"), float("inf")):
                with self.subTest(option=option, value=value):
                    with self.assertRaises(argparse.ArgumentTypeError):
                        INSTALLER.install(self.prefix, **{option: value})
                    self.assertFalse(self.prefix.exists())

    def test_timeouts_leave_no_installation_or_staging(self):
        for phase in ("Cargo build", "npm ci"):
            with self.subTest(phase=phase):
                results = [SystemExit(f"{phase} timed out after 1 seconds")]
                if phase == "npm ci":
                    results.insert(0, subprocess.CompletedProcess([], 0, self.artifact(self.stale)))
                with mock.patch.object(INSTALLER, "run_phase", side_effect=results):
                    with self.assertRaisesRegex(SystemExit, phase + " timed out"):
                        INSTALLER.install(self.prefix)
                self.assertFalse((self.prefix / "bin/oximut").exists())
                self.assertEqual(list((self.prefix / "lib").iterdir()), [])


class PhaseSupervision(unittest.TestCase):
    def test_deadline_kills_group_and_reaps_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "ready"
            # The descendant inherits stdout; killing only the parent would
            # leave communicate waiting on this pipe.
            child = "import time; time.sleep(30)"
            parent = ("import pathlib,subprocess,sys,time; "
                      f"child = subprocess.Popen([sys.executable, '-c', {child!r}]); "
                      f"pathlib.Path({str(ready)!r}).write_text(str(child.pid)); time.sleep(30)")
            popen = subprocess.Popen
            processes = []

            def spawn_process(*args, **kwargs):
                process = popen(*args, **kwargs)
                processes.append(process)
                return process

            with mock.patch.object(INSTALLER.subprocess, "Popen", side_effect=spawn_process) as spawn:
                with self.assertRaisesRegex(SystemExit, "test phase timed out after 2 seconds"):
                    INSTALLER.run_phase([sys.executable, "-c", parent], cwd=temporary,
                                        timeout_seconds=2, phase="test phase", capture_output=True)
            self.assertEqual(processes[0].returncode, -INSTALLER.signal.SIGKILL)
            self.assertTrue(ready.exists(), "fixture must spawn its descendant before timeout")
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            child_stat = Path("/proc") / ready.read_text() / "stat"
            deadline = time.monotonic() + 3
            while True:
                try:
                    # A dead orphan may remain a zombie until the host reaps it.
                    state = child_stat.read_text().rsplit(") ", 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break
                if time.monotonic() >= deadline:
                    self.fail(f"descendant survived the deadline with state {state}")
                time.sleep(0.05)

    def test_interruption_kills_group_and_reaps_before_reraising(self):
        process = mock.Mock(pid=12345)
        process.communicate.side_effect = [KeyboardInterrupt(), ("", None)]
        with mock.patch.object(INSTALLER.subprocess, "Popen", return_value=process), \
                mock.patch.object(INSTALLER.os, "killpg") as kill:
            with self.assertRaises(KeyboardInterrupt):
                INSTALLER.run_phase(["cargo"], cwd=".", timeout_seconds=3, phase="Cargo build")
        kill.assert_called_once_with(process.pid, INSTALLER.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_interrupted_group_signal_retries_before_reaping(self):
        process = mock.Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired("cargo", 3), ("", None)]
        with mock.patch.object(INSTALLER.subprocess, "Popen", return_value=process), \
                mock.patch.object(INSTALLER.os, "killpg", side_effect=
                                  [OSError(errno.EINTR, "interrupted"), None]) as kill:
            with self.assertRaisesRegex(SystemExit, "Cargo build timed out"):
                INSTALLER.run_phase(["cargo"], cwd=".", timeout_seconds=3, phase="Cargo build")
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(process.communicate.call_count, 2)
        process.kill.assert_not_called()

    def test_group_signal_failure_kills_child_and_bounds_reaping(self):
        for reap_error in (None, subprocess.TimeoutExpired("cargo", 5)):
            with self.subTest(reap_error=reap_error):
                process = mock.Mock(pid=12345, stdin=None, stderr=None)
                process.communicate.side_effect = subprocess.TimeoutExpired("cargo", 3)
                process.wait.side_effect = reap_error
                termination_error = OSError(errno.EPERM, "group signal denied")
                with mock.patch.object(INSTALLER.subprocess, "Popen", return_value=process), \
                        mock.patch.object(INSTALLER.os, "killpg", side_effect=termination_error):
                    with self.assertRaisesRegex(SystemExit, "Cargo build: process-group termination failed") as raised:
                        INSTALLER.run_phase(["cargo"], cwd=".", timeout_seconds=3, phase="Cargo build")
                self.assertIs(raised.exception.__cause__, termination_error)
                process.kill.assert_called_once_with()
                process.stdout.close.assert_called_once_with()
                process.wait.assert_called_once_with(timeout=5)
                self.assertEqual(process.communicate.call_count, 1)

    def test_deadline_communicates_after_killing_group(self):
        process = mock.Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired("cargo", 3), ("", None)]
        with mock.patch.object(INSTALLER.subprocess, "Popen", return_value=process), \
                mock.patch.object(INSTALLER.os, "killpg") as kill:
            with self.assertRaisesRegex(SystemExit, "Cargo build timed out after 3 seconds"):
                INSTALLER.run_phase(["cargo"], cwd=".", timeout_seconds=3, phase="Cargo build")
        kill.assert_called_once_with(process.pid, INSTALLER.signal.SIGKILL)
        self.assertEqual(process.communicate.call_args_list, [mock.call(timeout=3), mock.call()])


if __name__ == "__main__":
    unittest.main()
