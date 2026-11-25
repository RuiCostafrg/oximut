"""The installed smoke must prove the zygote served the mutants, not just that it was requested."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("smoke_install", ROOT / "scripts/smoke-install.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)

ON = "execution=persistent; isolation=fresh-test-forks; zygote=on; rolling-lanes=on"


def report(diagnostics=(ON,), hits=0):
    return {"diagnostics": list(diagnostics), "cache": {"outcomeHits": hits}}


class ZygoteSmokeCheck(unittest.TestCase):
    def counters(self, *files):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for index, content in enumerate(files):
            (Path(directory.name) / f"zygote-{index}.json").write_text(json.dumps(content))
        return Path(directory.name)

    def check(self, data, counters):
        return SMOKE.check_zygote(data, counters, "persistent/cold")

    def test_forked_workers_are_counted(self):
        self.assertEqual(self.check(report(), self.counters({"forks": 3, "zygoteFailures": 0})), 3)

    def test_requested_but_unavailable_zygote_fails(self):
        # An addon that cannot load falls back to normal workers and writes zero forks.
        with self.assertRaisesRegex(RuntimeError, "no mutant worker was forked"):
            self.check(report(), self.counters({"forks": 0, "zygoteFailures": 0}))
        with self.assertRaisesRegex(RuntimeError, "wrote no counters"):
            self.check(report(), self.counters())

    def test_zygote_failure_fails_even_with_forks(self):
        with self.assertRaisesRegex(RuntimeError, "zygote failures"):
            self.check(report(), self.counters({"forks": 2, "zygoteFailures": 1}))

    def test_report_must_say_zygote_on(self):
        for diagnostics in ((), ("execution=persistent; zygote=off (--no-zygote)",)):
            with self.assertRaisesRegex(RuntimeError, "zygote=on"):
                self.check(report(diagnostics), self.counters({"forks": 3}))

    def test_cached_outcomes_ran_no_mutants(self):
        self.assertIsNone(self.check(report(hits=2), self.counters()))
        with self.assertRaisesRegex(RuntimeError, "zygote=on"):
            self.check(report((), hits=2), self.counters())

    def test_prefix_without_addon_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "addon missing"):
                SMOKE.smoke(Path(directory), require_zygote=True)


if __name__ == "__main__":
    unittest.main()
