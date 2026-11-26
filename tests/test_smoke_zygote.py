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


def report(diagnostics=(ON,), executed=1, hits=0):
    """A report whose mutants were ``executed`` by workers and ``hits`` served from the outcome cache."""
    sources = ["executed"] * executed + ["cache"] * hits
    return {
        "diagnostics": list(diagnostics),
        "cache": {"outcomeHits": hits},
        "mutants": [{"provenance": {"source": source, "reason": "test"}} for source in sources],
    }


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

    def test_fully_cached_run_passes_without_forks(self):
        # Every mutant came from the outcome cache, so no worker existed to fork.
        self.assertIsNone(self.check(report(executed=0, hits=2), self.counters()))

    def test_fully_cached_run_must_still_say_zygote_on(self):
        with self.assertRaisesRegex(RuntimeError, "zygote=on"):
            self.check(report((), executed=0, hits=2), self.counters())

    def test_run_with_no_executed_mutant_and_no_cache_hit_fails(self):
        # Nothing ran and nothing was cached, so the zygote's use was never shown.
        with self.assertRaisesRegex(RuntimeError, "none was served from the cache"):
            self.check(report(executed=0, hits=0), self.counters())

    def test_executed_mutants_with_no_forks_fail(self):
        with self.assertRaisesRegex(RuntimeError, "no mutant worker was forked"):
            self.check(report(executed=3), self.counters({"forks": 0, "zygoteFailures": 0}))
        with self.assertRaisesRegex(RuntimeError, "wrote no counters"):
            self.check(report(executed=3), self.counters())

    def test_partially_cached_run_must_still_show_forks(self):
        with self.assertRaisesRegex(RuntimeError, "no mutant worker was forked"):
            self.check(report(executed=1, hits=2), self.counters({"forks": 0, "zygoteFailures": 0}))
        self.assertEqual(self.check(report(executed=1, hits=2), self.counters({"forks": 2})), 2)

    def test_prefix_without_addon_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "addon missing"):
                SMOKE.smoke(Path(directory), require_zygote=True)


class AutoWorkersSmokeCheck(unittest.TestCase):
    def test_resolved_automatic_count_is_returned(self):
        lines = [ON, "workers=4 (auto: 4 CPUs)"]
        self.assertEqual(SMOKE.check_auto_workers(report(lines), "persistent/auto"), 4)
        capped = ["workers=3 (auto: 16 CPUs, limited by 2048 MiB of memory)"]
        self.assertEqual(SMOKE.check_auto_workers(report(capped), "persistent/auto"), 3)

    def test_explicit_or_missing_count_fails(self):
        for diagnostics in ((), (ON,), ("workers=1 (explicit)",)):
            with self.assertRaisesRegex(RuntimeError, "auto"):
                SMOKE.check_auto_workers(report(diagnostics), "persistent/auto")


if __name__ == "__main__":
    unittest.main()
