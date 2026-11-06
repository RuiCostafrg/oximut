"""Corruption checks for the standalone pool diagnostic's independent gates."""
import copy
import unittest
import measure


def case(mode):
    """Produce a valid small record with independent job/worker/observation joins."""
    jobs, workers, observed, runs = [], [], [], []
    for i, (label, selector) in enumerate(zip(measure.LABELS, measure.SELECTORS)):
        t = i * 100
        job = {"pid": i + 100, "ppid": 999, "startTime": str(i + 1000), "requestedId": label,
               "assignedAt": t + 4, "spawnStartedAt": t if i == 0 else t - 83,
               "startSentAt": t + 10, "startedResponseAt": t + 12, "runSentAt": t + 15,
               "runResponseAt": t + 60, "runResolvedAt": t + 65, "drainedAt": t + 70,
               "runCallMs": 65, "drainMs": 5, "exitCode": None, "signal": "SIGTERM",
               "exitedAt": t + 68, "stopCompletedAt": t + 69, "preparedAsSpare": i > 0,
               "readyReceivedAt": t + 5 if i == 0 else t - 50, "noImportsBeforeAssignment": True}
        jobs.append(job)
        workers.append(copy.deepcopy(job))
        runs.append({"requestedId": label, "tests": [
            {"name": name, "state": "passed"} for name in measure.TESTS]})
        for phase in ("setup-file", "module", "state"):
            row = {"phase": phase, "requestedId": label, "activeMutant": selector,
                   "pid": job["pid"], "realm": "12345678-1234-4321-8234-" + str(i).zfill(12)}
            if phase == "state":
                row.update(local=0, cjs=0, esm=0, env=None, builtin=None, spy="original", fakeClock=False)
                row["global"] = None
            observed.append(row)
    if mode == "prewarm":
        workers.append({"pid": 104, "ppid": 999, "startTime": "1004", "disposedUnassigned": True,
                        "preparedAsSpare": True, "spawnStartedAt": 317,
                        "readyReceivedAt": 350, "exitedAt": 450, "stopCompletedAt": 451,
                        "exitCode": None, "signal": "SIGTERM", "noImportsBeforeAssignment": True})
    return {"mode": mode, "controllerPid": 999, "closed": True, "disposed": True,
            "freshnessAndHandoff": True, "cleanupErrors": [], "jobs": jobs, "observations": observed,
            "workerEvidence": workers, "runs": runs,
            "requested": [{"label": label, "activeMutant": selector}
                          for label, selector in zip(measure.LABELS, measure.SELECTORS)],
            "controllerStatus": {"status": {"code": 0, "signal": None}, "timedOut": False,
                                 "cleanupRequired": False, "cleanupVerified": True,
                                 "interrupted": False, "spawnError": None},
            "stages": {"initializeMs": 1}}


def row(pair, mode):
    return {"pair": pair, "mode": mode, "warmup": pair == 0, "case": case(mode),
            "wall_seconds": 1, "cpu_seconds": 1, "cpu_user_seconds": .8, "cpu_system_seconds": .2,
            "peak_aggregate_rss_bytes": 100, "peak_process_count": 3, "observed_process_count": 6,
            "success": True, "exit_code": 0, "timed_out": False, "spawn_error": None,
            "descendant_cleanup_required": False, "memory_errors": [],
            "memory_method": "sum-live-smaps_rollup-rss",
            "fixtureHashes": {name: "c" * 64 for name in measure.FIXTURE_FILES},
            "sourceHashes": {name: "d" * 64 for name in measure.SOURCE_FILES},
            "runtimeHashes": {name: "e" * 64 for name in ("nodeApi", "poolRunner", "realForkEntry")},
            "packageLockSha256": "f" * 64}


class Gates(unittest.TestCase):
    def test_raw_controller_selector_state_and_lifecycle_corruption_rejects(self):
        for mode in ("stock", "prewarm"):
            template = case(mode)
            measure.validate_case(template, mode)
            changes = [
                lambda c: c.update(closed=False),
                lambda c: c["controllerStatus"].update(cleanupRequired=True),
                lambda c: c["controllerStatus"]["status"].update(code=False),
                lambda c: c["observations"][0].update(activeMutant=""),
                lambda c: c["observations"][3].update(activeMutant=None),
                lambda c: c["observations"][0].update(pid=9999),
                lambda c: c["observations"][0].update(phase="module"),
                lambda c: c["jobs"][0].update(runResponseAt=0),
                lambda c: c["runs"][0]["tests"][0].update(name="different passing test"),
                lambda c: c["workerEvidence"][0].update(exitCode=1),
                lambda c: c["workerEvidence"][0].update(startTime="unrelated"),
                lambda c: c["workerEvidence"][0].update(ppid=5),
            ]
            for field in ("local", "cjs", "esm", "global", "env", "builtin", "spy", "fakeClock"):
                changes.append(lambda c, key=field: c["observations"][2].update({key: "dirty"}))
            if mode == "prewarm":
                changes.extend([
                    lambda c: c["workerEvidence"][-1].update(disposedUnassigned=False),
                    lambda c: c["workerEvidence"][-1].update(pid=c["workerEvidence"][0]["pid"]),
                    lambda c: c["workerEvidence"][-1].update(noImportsBeforeAssignment=False),
                ])
            for change in changes:
                corrupted = copy.deepcopy(template)
                change(corrupted)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    measure.validate_case(corrupted, mode)

    def test_pairing_requires_complete_equal_workloads_and_reports_no_product_goal(self):
        rows = [row(i, mode) for i in range(6) for mode in ("stock", "prewarm")]
        result = measure.summarize(rows)
        self.assertIsNone(result["oximut_vs_stryker"])
        self.assertIsNone(result["resources.phaseWallMs"])
        self.assertFalse(result["targets_met"])
        self.assertFalse(result["native_product_qualification"])
        for change in [
            lambda r: r.pop(), lambda r: r.__setitem__(1, r[0]),
            lambda r: r[0].update(success=False), lambda r: r[0].update(cpu_seconds=float("nan")),
            lambda r: r[0].update(descendant_cleanup_required=True),
            lambda r: r[0].update(peak_process_count=True),
            lambda r: r[0].update(fixtureHashes={"other": "input"}),
            lambda r: r[0].update(sourceHashes={"different": "probe"}),
            lambda r: r[0].update(runtimeHashes={"different": "Vitest"}),
            lambda r: r[0].update(sourceHashes={}),
            lambda r: r[0].update(warmup=False),
        ]:
            corrupted = copy.deepcopy(rows)
            change(corrupted)
            with self.assertRaises((ValueError, KeyError, TypeError)):
                measure.summarize(corrupted)


if __name__ == "__main__":
    unittest.main()
