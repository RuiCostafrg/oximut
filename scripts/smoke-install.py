#!/usr/bin/env python3
"""Smoke an installed prefix from a separate temporary project and working directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def smoke(prefix):
    prefix = prefix.resolve()
    with tempfile.TemporaryDirectory(prefix="oximut-installed-project-") as temporary:
        root = Path(temporary)
        (root / "node_modules").symlink_to(prefix / "lib" / "oximut" / "node_modules", target_is_directory=True)
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
        hook = root / "probe.mjs"
        observed = root / "adapter-paths.jsonl"
        hook.write_text(
            'import { appendFileSync } from "node:fs";\n'
            r'if (/\/m[12]-runner\.mjs$/.test(process.argv[1] ?? "")) '
            f'appendFileSync({json.dumps(str(observed))}, JSON.stringify(process.argv[1]) + "\\n");\n'
        )
        environment = dict(os.environ)
        environment["NODE_OPTIONS"] = f"{environment.get('NODE_OPTIONS', '')} --import={hook.as_uri()}".strip()
        for execution in ("fresh", "persistent"):
            observed.unlink(missing_ok=True)
            report = root / f"{execution}.json"
            result = subprocess.run([
                str(prefix / "bin" / "oximut"), "--root", str(root),
                "--config", "vitest.config.mjs", "--source", "subject.js",
                "--execution", execution, "--report", str(report),
            ], cwd=root, env=environment, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                raise RuntimeError(f"{execution} exited {result.returncode}: {result.stdout}\n{result.stderr}")
            runner = "m2-runner.mjs" if execution == "persistent" else "m1-runner.mjs"
            expected = str(prefix / "lib/oximut/packages/vitest-adapter" / runner)
            paths = [json.loads(line) for line in observed.read_text().splitlines()]
            if not paths or any(path != expected for path in paths):
                raise RuntimeError(f"{execution} used unexpected adapter paths: {paths}")
            data = json.loads(report.read_text())
            if data["baseline"]["outcome"] != "passed" or not data["summary"]["complete"]:
                raise RuntimeError(f"{execution} incomplete: {data}")
            if data["summary"]["killed"] < 1:
                raise RuntimeError(f"{execution} did not execute and kill a mutant: {data}")
            print(f"{execution}: baseline passed, {data['summary']['killed']} mutants killed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, type=Path)
    smoke(parser.parse_args().prefix)
