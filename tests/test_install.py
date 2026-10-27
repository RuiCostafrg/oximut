"""Cheap source installer and launcher checks; full installation smoke is scripts/smoke-install.py."""
import importlib.util
import json
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
        with mock.patch.object(INSTALLER.subprocess, "run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, output),
                               subprocess.CompletedProcess([], 0)]
            INSTALLER.install(self.prefix)
        self.assertEqual((self.prefix / "lib/oximut/oximut").read_text(), executable.read_text())
        command = run.call_args_list[0].args[0]
        for argument in ("--locked", "--release", "--message-format=json-render-diagnostics"):
            self.assertIn(argument, command)

    def test_missing_artifact_never_falls_back_to_stale_binary(self):
        with mock.patch.object(INSTALLER.subprocess, "run", return_value=
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
                with mock.patch.object(INSTALLER.subprocess, "run", return_value=
                                       subprocess.CompletedProcess([], 0, output)) as run:
                    with self.assertRaisesRegex(SystemExit, error):
                        INSTALLER.install(self.prefix)
                self.assertEqual(run.call_count, 1)
                self.assertFalse((self.prefix / "lib/oximut").exists())

    def test_failed_build_never_installs_reported_artifact(self):
        with mock.patch.object(INSTALLER.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 1, self.artifact(self.stale))) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                INSTALLER.install(self.prefix)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.prefix / "lib/oximut").exists())


if __name__ == "__main__":
    unittest.main()
