"""Zygote addon build helper: real build when possible, graceful skips otherwise."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zygote_addon_build", ROOT / "scripts/build-zygote-addon.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)
SOURCE = ROOT / "packages/vitest-adapter/native/zygote-fork.c"


def glibc_node_path():
    """PATH in which a glibc Node with headers is found first, or None."""
    candidates = [os.environ.get("PATH", "")]
    import glob
    candidates += [f"{d}:{os.environ.get('PATH', '')}" for d in glob.glob("/tmp/oximut-node.*/bin")]
    for path in candidates:
        with mock.patch.dict(os.environ, {"PATH": path}):
            node = shutil.which("node")
            if node is None or not BUILD.is_glibc():
                continue
            prefix = Path(os.path.realpath(node)).parent.parent
            if (prefix / "include/node/node_api.h").is_file() and shutil.which("cc"):
                probe = subprocess.run([node, "-e", "0"], capture_output=True)
                if probe.returncode == 0:
                    return path
    return None


def skip_unless_required(test, reason):
    """CI sets OXIMUT_REQUIRE_ZYGOTE_ADDON=1 so a missing toolchain fails instead of skipping."""
    if os.environ.get("OXIMUT_REQUIRE_ZYGOTE_ADDON") == "1":
        test.fail(f"zygote addon build required: {reason}")
    test.skipTest(reason)


class ZygoteAddonBuild(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "build/zygote-fork.node"

    def test_real_build_produces_loadable_addon(self):
        path = glibc_node_path()
        if path is None:
            skip_unless_required(self, "no glibc Node with headers and C compiler")
        with mock.patch.dict(os.environ, {"PATH": path}):
            built, reason = BUILD.build_addon(SOURCE, self.output)
            self.assertTrue(built, reason)
            check = subprocess.run(
                ["node", "-e", "const a=require(process.argv[1]); if(typeof a.fork!=='function') process.exit(1)",
                 str(self.output)], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual([p.name for p in self.output.parent.iterdir()], ["zygote-fork.node"])

    def test_compiler_failure_reports_reason_and_leaves_no_output(self):
        if not BUILD.is_glibc() or shutil.which("node") is None:
            skip_unless_required(self, "needs glibc and node to reach the compile step")
        with mock.patch.dict(os.environ, {"CC": "false"}):
            with mock.patch.object(BUILD, "is_glibc", return_value=True):
                built, reason = BUILD.build_addon(SOURCE, self.output, node=shutil.which("node"))
        self.assertFalse(built)
        self.assertTrue(reason)
        self.assertFalse(self.output.exists())
        if self.output.parent.exists():
            self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_non_linux_is_skipped(self):
        with mock.patch.object(BUILD.sys, "platform", "darwin"):
            built, reason = BUILD.build_addon(SOURCE, self.output)
        self.assertFalse(built)
        self.assertIn("darwin", reason)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
