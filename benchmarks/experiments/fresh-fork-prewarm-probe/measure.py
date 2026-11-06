#!/usr/bin/env python3
"""Paired native diagnostic of the standalone pool probe, never the product goal."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "benchmarks"))
import run as measurement
import targets

LABELS = ("baseline", "mutant-alpha", "null", "mutant-beta")
SELECTORS = (None, "a" * 64, None, "b" * 64)
TESTS = ("builtin clean", "env clean", "external CJS clean", "external ESM clean",
         "global clean", "local ESM clean", "spy clean", "timer clean")
METRICS = ("wall_seconds", "cpu_seconds", "peak_aggregate_rss_bytes")
DISPLAY = (*METRICS, "cpu_user_seconds", "cpu_system_seconds",
           "peak_process_count", "observed_process_count")
SOURCE_FILES = {"probe.mjs", "pool.mjs", "launcher.mjs", "test.mjs", "measure.py",
                "test_measurement.py", "fixtures/setup.mjs", "fixtures/fixture.test.mjs"}
FIXTURE_FILES = {"package.json", "local.mjs", "setup.mjs", "fixture.test.mjs",
                 *{f"node_modules/{name}/{file}" for name in ("stateful-cjs", "stateful-esm")
                   for file in ("package.json", "index.js")}}


def require(condition, message):
    """Reject incomplete or contradictory evidence."""
    if not condition:
        raise ValueError(message)


def validate_case(case, mode):
    """Independently reconcile raw fixture identities, state, selectors and cleanup."""
    status = case["controllerStatus"]
    require(case["mode"] == mode and case["closed"] is True and case["disposed"] is True
            and case["freshnessAndHandoff"] is True and case["cleanupErrors"] == []
            and "failure" not in case, "unsuccessful controller result")
    require(status["status"] == {"code": 0, "signal": None}
            and type(status["status"]["code"]) is int
            and status["timedOut"] is False and status["cleanupRequired"] is False
            and status["cleanupVerified"] is True and status["interrupted"] is False
            and status["spawnError"] is None, "invalid controller lifecycle")
    jobs, observed, workers = case["jobs"], case["observations"], case["workerEvidence"]
    require(len(jobs) == 4 and len(observed) == 12 and len(case["runs"]) == 4, "wrong fixture inventory")
    require(len(workers) == (4 if mode == "stock" else 5), "wrong executed/unused worker inventory")
    require(case["requested"] == [
        {"label": label, "activeMutant": selector} for label, selector in zip(LABELS, SELECTORS)
    ], "changed requested selector sequence")
    for index, (label, selector) in enumerate(zip(LABELS, SELECTORS)):
        job = jobs[index]
        require(job["requestedId"] == label and type(job["pid"]) is int and job["pid"] > 0
                and job["ppid"] == case["controllerPid"] and job["startTime"].isascii()
                and job["startTime"].isdecimal(), "invalid worker identity")
        require(case["runs"][index] == {"requestedId": label, "tests": [
            {"name": name, "state": "passed"} for name in TESTS
        ]}, "changed test identities/outcomes")
        group = observed[index * 3:index * 3 + 3]
        require([row["phase"] for row in group] == ["setup-file", "module", "state"],
                "wrong setup/module/state order")
        for row in group:
            require(row["pid"] == job["pid"] and row["requestedId"] == label
                    and row["activeMutant"] == selector and row["realm"] == group[0]["realm"],
                    "unrelated or stale project observation")
            require(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                                 row["realm"]) is not None, "malformed realm UUID")
        for field, expected in {"local": 0, "cjs": 0, "esm": 0, "global": None,
                                "env": None, "builtin": None, "spy": "original", "fakeClock": False}.items():
            require(field in group[2] and type(group[2][field]) is type(expected)
                    and group[2][field] == expected, "dirty state: " + field)
        matching = [w for w in workers if w.get("requestedId") == label]
        require(len(matching) == 1 and matching[0]["pid"] == job["pid"]
                and matching[0]["startTime"] == job["startTime"], "job/worker evidence mismatch")
        clocks = ["spawnStartedAt", "assignedAt", "startSentAt", "startedResponseAt",
                  "runSentAt", "runResponseAt", "runResolvedAt", "drainedAt",
                  "runCallMs", "drainMs"]
        require(all(type(job[key]) in (int, float) and math.isfinite(job[key]) and job[key] >= 0
                    for key in clocks), "invalid job clock")
        require(job["assignedAt"] <= job["startSentAt"] <= job["startedResponseAt"]
                <= job["runSentAt"] <= job["runResponseAt"] <= job["runResolvedAt"]
                <= job["drainedAt"], "contradictory job clocks")
    require(len({job["pid"] for job in jobs}) == 4
            and len({row["realm"] for row in observed if row["phase"] == "module"}) == 4,
            "reused process or realm")
    intervals = []
    require(len({worker["pid"] for worker in workers}) == len(workers), "duplicate worker evidence")
    for worker in workers:
        require(type(worker["pid"]) is int and worker["pid"] > 0
                and worker["ppid"] == case["controllerPid"] and worker["startTime"].isascii()
                and worker["startTime"].isdecimal(), "invalid executed or unused worker identity")
        require(type(worker.get("exitCode")) is int and worker["exitCode"] == 0 or
                (worker.get("exitCode") is None and worker.get("signal") == "SIGTERM"),
                "abnormal worker exit")
        require(worker["stopCompletedAt"] >= worker["exitedAt"], "stop precedes exit")
        if mode == "prewarm":
            require(worker["noImportsBeforeAssignment"] is True, "unused worker imported project")
            require(worker["readyReceivedAt"] >= worker["spawnStartedAt"], "invalid readiness timing")
            if worker["preparedAsSpare"]:
                if "assignedAt" not in worker:
                    require(worker.get("disposedUnassigned") is True
                            and "requestedId" not in worker, "unused spare was not disposed")
                end = worker.get("assignedAt", worker["stopCompletedAt"])
                intervals.extend([(worker["spawnStartedAt"], 1), (end, -1)])
    pending = 0
    for _, delta in sorted(intervals):
        pending += delta
        require(0 <= pending <= 1, "more than one unused spare")
    require(pending == 0, "unused spare remains")
    return case


def validate_row(row):
    """Require complete process-tree resource evidence with unchanged sampler semantics."""
    require(row["success"] is True and row["exit_code"] == 0 and row["timed_out"] is False
            and row["spawn_error"] is None and row["descendant_cleanup_required"] is False
            and row["memory_errors"] == [] and not row.get("cleanup_error")
            and row["memory_method"] == "sum-live-smaps_rollup-rss", "invalid resource measurement")
    for key in DISPLAY:
        value = row[key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "invalid metric: " + key)
    require(all(type(row[key]) is int for key in ("peak_process_count", "observed_process_count")),
            "process counts must be integers")
    require(row["wall_seconds"] > 0 and row["cpu_seconds"] > 0
            and row["peak_aggregate_rss_bytes"] > 0 and row["peak_process_count"] >= 1
            and row["observed_process_count"] >= row["peak_process_count"], "empty resource evidence")
    require(math.isclose(row["cpu_seconds"], row["cpu_user_seconds"] + row["cpu_system_seconds"]),
            "CPU total differs from user/system")
    for key, names in (("sourceHashes", SOURCE_FILES), ("fixtureHashes", FIXTURE_FILES),
                       ("runtimeHashes", {"nodeApi", "poolRunner", "realForkEntry"})):
        values = row[key]
        require(set(values) == names and all(isinstance(value, str) and
                re.fullmatch(r"[0-9a-f]{64}", value) for value in values.values()),
                "incomplete declared hashes: " + key)
    require(isinstance(row["packageLockSha256"], str) and
            re.fullmatch(r"[0-9a-f]{64}", row["packageLockSha256"]), "invalid lockfile hash")


def summarize(rows):
    """Pair equal native probe workloads; do not produce Oximut/Stryker ratios."""
    require(len(rows) == 12, "require one warmup and five measured pairs")
    indexed = {(row["pair"], row["mode"]): row for row in rows}
    require(len(indexed) == 12, "duplicate pair/mode")
    for index in range(6):
        for mode in ("stock", "prewarm"):
            row = indexed[index, mode]
            validate_row(row)
            require(row["warmup"] is (index == 0), "incorrect warmup label")
            validate_case(row["case"], mode)
    for field in ("fixtureHashes", "sourceHashes", "runtimeHashes", "packageLockSha256"):
        values = [json.dumps(row[field], sort_keys=True) for row in rows]
        require(len(set(values)) == 1, "unequal input closure: " + field)
    measured = [row for row in rows if not row["warmup"]]
    return {
        "scope": "Standalone four-job Vitest pool fixture, including Node probe/controller/setup/teardown; not the Oximut CLI or colord",
        "correctness_gate": {"structural_passed": True, "colord_102_tests_10_mutants_run": False},
        "warmup_pairs": 1, "measured_pairs": 5,
        "variants": {mode: {
            "medians": {key: statistics.median(row[key] for row in measured if row["mode"] == mode)
                        for key in DISPLAY},
            "controller_stage_medians_ms": {
                key: statistics.median(row["case"]["stages"][key] for row in measured if row["mode"] == mode)
                for key in measured[0]["case"]["stages"]},
            "job_stage_medians_ms": {label: {
                key: statistics.median(row["case"]["jobs"][i][key] for row in measured if row["mode"] == mode)
                for key in ("runCallMs", "drainMs")}
                for i, label in enumerate(LABELS)},
        } for mode in ("stock", "prewarm")},
        "prewarm_vs_stock_probe": {
            key: targets.paired_interval([(indexed[i, "prewarm"][key], indexed[i, "stock"][key])
                                         for i in range(1, 6)]) for key in METRICS},
        "oximut_vs_stryker": None, "resources.phaseWallMs": None, "targets_met": False,
        "native_product_qualification": False,
        "limits": "Process-tree CPU includes the probe and its reaped controllers/forks. External sampler CPU is excluded and perturbs wall. Stages are nested diagnostic intervals. Independent medians do not add. No product performance qualification.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, summary = [], {"targets_met": False}
    try:
        node = shutil.which("node")
        require(node is not None, "Node required")
        metadata = {"host": platform.uname()._asdict(),
                    "node": subprocess.check_output([node, "--version"], text=True).strip(),
                    "node_executable": str(Path(node).resolve(strict=True)),
                    "node_sha256": hashlib.sha256(Path(node).resolve(strict=True).read_bytes()).hexdigest(),
                    "python": sys.version,
                    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    "git_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
                    "input_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in [*HERE.glob("*.mjs"), *HERE.glob("*.py"),
                                               *HERE.glob("fixtures/*.mjs"),
                                               ROOT / "benchmarks/run.py", ROOT / "benchmarks/targets.py",
                                               ROOT / "package-lock.json", ROOT / ".node-version"]}}
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        env = {**os.environ, "NODE_OPTIONS": ""}
        for pair in range(6):
            order = ("stock", "prewarm") if pair % 2 == 0 else ("prewarm", "stock")
            for mode in order:
                folder = output / (str(pair) + "-" + mode)
                command = [node, str(HERE / "probe.mjs"), "--case", mode, "--output", str(folder)]
                row = measurement.run_command(command, ROOT, output / (str(pair) + "-" + mode + ".log"),
                                              timeout=60, env=env)
                row.update(pair=pair, warmup=pair == 0, mode=mode)
                rows.append(row)
                (output / "measurements.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
                require(row["success"] is True, "failed whole-command resource capture")
                artifact = json.loads((folder / "evidence.json").read_text())
                require(artifact.get("selectedCasesPassed") is True and not artifact.get("failure")
                        and len(artifact["cases"]) == 1, "probe did not pass")
                row["case"] = validate_case(artifact["cases"][0], mode)
                for field in ("sourceHashes", "runtimeHashes", "packageLockSha256"):
                    row[field] = artifact[field]
                row["fixtureHashes"] = row["case"]["fixtureHashes"]
                require(artifact["node"] == metadata["node"], "Node version changed during campaign")
                for name, digest in row["sourceHashes"].items():
                    require(digest == metadata["input_sha256"][str((HERE / name).relative_to(ROOT))],
                            "probe source changed during campaign")
                require(row["packageLockSha256"] == metadata["input_sha256"]["package-lock.json"],
                        "lockfile changed during campaign")
                validate_row(row)
                (output / "measurements.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        summary = summarize(rows)
    except Exception as error:
        summary["failure"] = str(error)
    finally:
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 1 if "failure" in summary else 0


if __name__ == "__main__":
    raise SystemExit(main())
