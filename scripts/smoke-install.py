#!/usr/bin/env python3
"""Smoke an installed prefix from a separate temporary project and working directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile


SPEC = importlib.util.spec_from_file_location("candidate_sandbox", Path(__file__).with_name("candidate-sandbox.py"))
SANDBOX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SANDBOX)


def project_dependencies(prefix, root, isolated):
    installed = prefix / "lib/oximut/node_modules"
    if isolated:
        # Vite writes .vite-temp beside the project's dependencies. Keep those
        # writes private without making any installed candidate assets writable.
        shutil.copytree(installed, root / "node_modules", symlinks=True)
    else:
        (root / "node_modules").symlink_to(installed, target_is_directory=True)


ADDON = Path("lib/oximut/packages/vitest-adapter/native/build/zygote-fork.node")


def check_zygote(data, counters_dir, label):
    """Assert a persistent run really forked its mutant workers from the zygote.

    ``zygote=on`` only says the mode was requested. A missing or unloadable addon
    falls back to normal workers and still passes, so the pool's own counters
    (written when the controller exits) must show forks and no zygote failure.
    Returns the number of zygote forks.
    """
    diagnostics = data.get("diagnostics", [])
    if not any("zygote=on" in line for line in diagnostics):
        raise RuntimeError(f"{label}: report lacks the zygote=on diagnostic: {diagnostics}")
    if (data.get("cache") or {}).get("outcomeHits"):
        return None  # Served from the outcome cache: no mutant ran.
    files = sorted(counters_dir.iterdir())
    if not files:
        raise RuntimeError(f"{label}: zygote=on but the pool wrote no counters (addon unavailable?)")
    totals = {}
    for path in files:
        for key, value in json.loads(SANDBOX.read_regular(path)).items():
            totals[key] = totals.get(key, 0) + value
    if totals.get("forks", 0) <= 0:
        raise RuntimeError(f"{label}: zygote=on but no mutant worker was forked from it: {totals}")
    if totals.get("zygoteFailures", 0):
        raise RuntimeError(f"{label}: zygote failures: {totals}")
    return totals["forks"]


def smoke(prefix, *, reports_dir=None, validator=None, isolated=False, require_zygote=False):
    if (reports_dir is None) != (validator is None):
        raise ValueError("reports-dir and validator must be supplied together")
    if reports_dir is not None:
        reports_dir = reports_dir.resolve()
        reports_dir.mkdir(parents=True, exist_ok=False)
        validator = validator.resolve()
    results = []
    prefix = prefix.resolve()
    if require_zygote and not (prefix / ADDON).is_file():
        raise RuntimeError(f"zygote addon missing from the installed prefix: {prefix / ADDON}")
    with tempfile.TemporaryDirectory(prefix="oximut-installed-project-") as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        project_dependencies(prefix, root, isolated)
        (root / "package.json").write_text('{"type":"module"}\n')
        (root / "vitest.config.mjs").write_text('export default { test: { include: ["subject.test.js"] } };\n')
        (root / "subject.js").write_text('export const positive = value => value > 0;\n')
        (root / "subject.test.js").write_text(
            'import { test, expect } from "vitest";\n'
            'import { positive } from "./subject.js";\n'
            'test("positive", () => { expect(positive(1)).toBe(true); expect(positive(0)).toBe(false); });\n'
        )
        # Record the real Node entrypoint, so a checkout-relative fallback cannot
        # accidentally make an apparently independent installation pass.
        hook = Path(temporary) / "probe.mjs"
        observed = Path(temporary) / "adapter-paths.jsonl"
        hook.write_text(
            'import { appendFileSync } from "node:fs";\n'
            r'if (/\/(m1-runner\.mjs|m2-worker\.ts)$/.test(process.argv[1] ?? "")) '
            f'appendFileSync({json.dumps(str(observed))}, JSON.stringify(process.argv[1]) + "\\n");\n'
        )
        environment = dict(os.environ)
        environment["NODE_OPTIONS"] = f"{environment.get('NODE_OPTIONS', '')} --import={hook.as_uri()}".strip()
        for execution in ("fresh", "persistent"):
            # Give each mode a cold cache before testing reuse and invalidation.
            shutil.rmtree(root / ".oximut", ignore_errors=True)
            cases = ("cold", "warm", "force", "source", "test", "config") if reports_dir else ("smoke",)
            expected_summary = None
            for case in cases:
                if case in ("source", "test", "config"):
                    changed = {"source": "subject.js", "test": "subject.test.js",
                               "config": "vitest.config.mjs"}[case]
                    with (root / changed).open("a") as stream:
                        stream.write(f"\n// R3 {execution} {case} invalidation\n")
                observed.unlink(missing_ok=True)
                # Keep report identity stable across cache pairs; archive outside
                # project inputs only after each command has fully exited.
                report = Path(temporary) / "live-report.json"
                report.unlink(missing_ok=True)
                counters = Path(temporary) / "zygote-counters"
                shutil.rmtree(counters, ignore_errors=True)
                counters.mkdir()
                arguments = ["--cache", "--cache-inputs-complete", "--cache-policy", "always"] if reports_dir else []
                if case == "force":
                    arguments.append("--force")
                command = [
                    str(prefix / "bin" / "oximut"), "--root", str(root),
                    "--config", "vitest.config.mjs", "--source", "subject.js",
                    "--execution", execution, "--workers", "1", "--report", str(report), *arguments,
                ]
                if isolated:
                    SANDBOX.run(command, cwd=root, writable=[Path(temporary)],
                                readonly=[prefix], node_options=f"--import={hook.as_uri()}",
                                env={"OXIMUT_ZYGOTE_COUNTERS_DIR": counters})
                    # Namespace has exited and all descendants are dead before any reads/writes.
                    SANDBOX.safe_tree(Path(temporary), external_links=[(root / "node_modules", prefix / "lib/oximut/node_modules")])
                else:
                    result = subprocess.run(command, cwd=root,
                                            env={**environment, "OXIMUT_ZYGOTE_COUNTERS_DIR": str(counters)},
                                            capture_output=True, text=True, timeout=300)
                    if result.returncode != 0:
                        raise RuntimeError(f"{execution}/{case} exited {result.returncode}: {result.stdout}\n{result.stderr}")
                # Persistent execution drives the controller natively (no relay).
                runner = "m2-worker.ts" if execution == "persistent" else "m1-runner.mjs"
                expected = str(prefix / "lib/oximut/packages/vitest-adapter" / runner)
                paths = [json.loads(line) for line in SANDBOX.read_regular(observed).decode().splitlines()]
                if not paths or any(path != expected for path in paths):
                    raise RuntimeError(f"{execution} used unexpected adapter paths: {paths}")
                data = json.loads(SANDBOX.read_regular(report))
                if data["baseline"]["outcome"] != "passed" or not data["summary"]["complete"]:
                    raise RuntimeError(f"{execution}/{case} incomplete: {data}")
                if data["summary"]["killed"] < 1:
                    raise RuntimeError(f"{execution}/{case} did not kill a mutant: {data}")
                summary = {key: data["summary"][key] for key in ("total", "killed", "survived")}
                if expected_summary is not None and summary != expected_summary:
                    raise RuntimeError(f"cache changed outcomes: {summary} != {expected_summary}")
                expected_summary = summary
                if reports_dir:
                    hits = data["cache"]["outcomeHits"]
                    if (case == "warm" and hits < 1) or (case != "warm" and hits != 0):
                        raise RuntimeError(f"unexpected {execution}/{case} cache hits: {hits}")
                    archived = reports_dir / f"{execution}-{case}-report.json"
                    archived.write_bytes(SANDBOX.read_regular(report))
                    subprocess.run(["node", str(validator), str(archived)], cwd=validator.parent,
                                   check=True, timeout=30)
                forks = None
                if require_zygote and execution == "persistent":
                    forks = check_zygote(data, counters, f"{execution}/{case}")
                results.append({"execution": execution, "case": case, "summary": summary,
                                "cache": data.get("cache"), "zygoteForks": forks,
                                "report": str(archived if reports_dir else report)})
                note = ""
                if require_zygote and execution == "persistent":
                    note = f", zygote=on ({forks} forked workers)" if forks else ", zygote=on (cached outcomes)"
                print(f"{execution}/{case}: baseline passed, {data['summary']['killed']} mutants killed{note}")
        if reports_dir:
            (reports_dir / "tests.json").write_text(json.dumps(results, indent=2) + "\n")
        return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--validator", type=Path, help="strict validate-reports.mjs entrypoint")
    parser.add_argument("--isolated", action="store_true", help="require Linux bubblewrap boundary")
    parser.add_argument("--require-zygote", action="store_true",
                        help="require the zygote addon and zygote-forked mutant workers (Linux glibc hosts)")
    args = parser.parse_args()
    smoke(args.prefix, reports_dir=args.reports_dir, validator=args.validator, isolated=args.isolated,
          require_zygote=args.require_zygote)
