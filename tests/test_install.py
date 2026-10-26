"""Cheap installation launcher checks; full installation smoke is scripts/smoke-install.py."""
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "oximut_launcher", Path(__file__).resolve().parents[1] / "scripts" / "oximut-launcher.py")
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


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


if __name__ == "__main__":
    unittest.main()
