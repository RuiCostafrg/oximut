"""M4 cache acceptance: reuse must preserve outcomes and validate every input."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

from test_m1 import BINARY, REPO


FAKE_ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
const [input, output] = process.argv.slice(-2);
const request = JSON.parse(readFileSync(input, 'utf8'));
if (process.env.OXIMUT_CACHE_CALLS) {
  appendFileSync(process.env.OXIMUT_CACHE_CALLS, `${request.kind}:${request.jobId}\n`);
}
const response = {
  protocol: request.protocol, runId: request.runId, jobId: request.jobId,
  generation: request.generation, outcome: 'passed',
  tests: { passed: 2, failed: 0, skipped: 0 }, errors: [],
};
const control = process.env.OXIMUT_CACHE_CONTROL;
const command = control && existsSync(control) ? readFileSync(control, 'utf8').trim() : '';
if (request.collectCoverage) {
  const mutants = request.collectCoverage;
  response.coverage = {
    complete: true,
    tests: [
      { id: 'test-1', file: `${request.root}/test/subject.test.ts`, name: 'first' },
      { id: 'test-2', file: `${request.root}/test/subject.test.ts`, name: 'second' },
    ],
    hits: mutants.map(m => ({ mutantId: m.id, tests: ['test-1'], outsideTest: false })),
    reasons: [],
  };
}
if (request.kind === 'baseline' && command.startsWith('edit-baseline-once|')) {
  const [, relativePath, contents] = command.split('|');
  writeFileSync(`${request.root}/${relativePath}`, contents);
  unlinkSync(control);
} else if (request.kind === 'baseline' && command.startsWith('edit-baseline-twice|')) {
  const [, relativePath, first, second] = command.split('|');
  writeFileSync(`${request.root}/${relativePath}`, first);
  writeFileSync(control, `edit-baseline-second|${relativePath}|${second}`);
} else if (request.kind === 'baseline' && command.startsWith('edit-baseline-second|')) {
  const [, relativePath, contents] = command.split('|');
  writeFileSync(`${request.root}/${relativePath}`, contents);
  unlinkSync(control);
} else if (request.kind === 'baseline' && command.startsWith('delete-baseline-input-once|')) {
  const [, inputPath] = command.split('|');
  unlinkSync(inputPath);
  unlinkSync(control);
} else if (request.kind === 'baseline' && command === 'fail-baseline-once') {
  unlinkSync(control);
  response.outcome = 'failed';
  response.tests = { passed: 1, failed: 1, skipped: 0 };
  response.errors = ['injected baseline failure'];
} else if (request.kind === 'mutant' && command === 'fail-mutants-once') {
  unlinkSync(control);
  response.outcome = 'infrastructure-error';
  response.tests = { passed: 0, failed: 0, skipped: 0 };
  response.errors = ['injected failure'];
} else if (request.kind === 'mutant' && process.env.OXIMUT_FAKE_MUTANT_MODE === 'failed') {
  response.outcome = 'failed';
  response.tests = { passed: 1, failed: 1, skipped: 0 };
  response.errors = ['injected failing test'];
}
writeFileSync(output, JSON.stringify(response));
"""


FAKE_PERSISTENT_ADAPTER = r"""
import { appendFileSync, existsSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const session = process.argv[2];
const control = process.env.OXIMUT_PERSISTENT_CONTROL;
let sequence = 0;
for (;;) {
  if (existsSync(join(session, 'stop'))) {
    const command = control && existsSync(control) ? readFileSync(control, 'utf8').trim() : '';
    if (command === 'shutdown-fail') process.exit(23);
    if (command.startsWith('edit-on-stop|')) {
      const [, relativePath, contents] = command.split('|');
      writeFileSync(`${process.env.OXIMUT_PERSISTENT_ROOT}/${relativePath}`, contents);
      unlinkSync(control);
    }
    process.exit(0);
  }
  const input = join(session, `request-${sequence}.json`);
  if (!existsSync(input)) { await new Promise(r => setTimeout(r, 5)); continue; }
  const request = JSON.parse(readFileSync(input, 'utf8'));
  if (process.env.OXIMUT_CACHE_CALLS) {
    appendFileSync(process.env.OXIMUT_CACHE_CALLS, `${request.kind}:${request.jobId}\n`);
  }
  const requestCommand = control && existsSync(control) ? readFileSync(control, 'utf8').trim() : '';
  const failBaseline = request.kind === 'baseline' && requestCommand === 'baseline-fail-once';
  const response = { protocol: request.protocol, runId: request.runId,
    jobId: request.jobId, generation: request.generation,
    outcome: failBaseline ? 'failed' : 'passed',
    tests: failBaseline ? { passed: 1, failed: 1, skipped: 0 } : { passed: 2, failed: 0, skipped: 0 },
    errors: failBaseline ? ['injected baseline failure'] : [] };
  if (failBaseline) unlinkSync(control);
  const output = join(session, `response-${sequence++}.json`);
  writeFileSync(output + '.tmp', JSON.stringify(response));
  renameSync(output + '.tmp', output);
}
"""


class IncrementalCache(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oximut-m4-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        shutil.copytree(REPO / "fixtures/m1", self.root,
                        ignore=shutil.ignore_patterns("node_modules", ".vitest"))
        adapter_dir = self.base / "adapter"
        adapter_dir.mkdir()
        self.adapter = adapter_dir / "adapter.mjs"
        self.adapter.write_text(FAKE_ADAPTER)
        self.calls = self.base / "calls.log"
        self.control = self.base / "control.txt"
        self.report_path = self.base / "report.json"

    def run_oximut(self, *extra, env=None, success=True, cache_policy="always"):
        command = [
            str(BINARY), "--root", str(self.root), "--config", "vitest.config.ts",
            "--source", "src/subject.ts", "--adapter", str(self.adapter),
            "--report", str(self.report_path), "--cache", "--cache-inputs-complete",
            "--cache-policy", cache_policy,
            *map(str, extra),
        ]
        process_env = {**os.environ, "OXIMUT_CACHE_CALLS": str(self.calls),
                       "OXIMUT_CACHE_CONTROL": str(self.control)}
        if env:
            process_env.update(env)
        result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                                timeout=60, env=process_env)
        if success:
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        else:
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return json.loads(self.report_path.read_text())

    def read_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def policy_history_files(self):
        return list((self.root / ".oximut/cache/policy").glob("*.json"))

    def tamper_policy_history(self, **changes):
        files = self.policy_history_files()
        self.assertTrue(files, "a complete auto-policy run should write advisory history")
        path = files[0]
        history = json.loads(path.read_text())
        history.update(changes)
        path.write_text(json.dumps(history))
        return path

    @staticmethod
    def outcomes(report):
        return {item["id"]: item["outcome"] for item in report["mutants"]}

    def test_real_vitest_cached_and_forced_runs_agree_in_fresh_and_persistent_modes(self):
        # Keep this corpus on real Vitest: the fake-adapter cases below isolate
        # fingerprint inputs, while this one checks the observable test result.
        root = self.base / "vitest-project"
        shutil.copytree(REPO / "fixtures/m1", root,
                        ignore=shutil.ignore_patterns("node_modules", ".vitest"))
        (root / "node_modules").symlink_to(REPO / "node_modules")

        def run(*extra):
            command = [str(BINARY), "--root", str(root), "--config", "vitest.config.ts",
                       "--source", "src/subject.ts", "--report", str(self.base / "vitest-report.json"),
                       "--cache", "--cache-inputs-complete", "--cache-policy", "always", *extra]
            result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                                    timeout=180, env=os.environ.copy())
            self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
            return json.loads((self.base / "vitest-report.json").read_text())

        fresh = run("--execution", "fresh")
        fresh_warm = run("--execution", "fresh")
        fresh_forced = run("--execution", "fresh", "--force")
        selected = run("--execution", "fresh", "--test-selection")
        parallel = ("--execution", "persistent", "--test-selection", "--workers", "2")
        persistent_selected = run(*parallel)
        persistent_warm = run(*parallel)
        persistent_forced = run(*parallel, "--force")
        baseline = self.outcomes(fresh)
        for report in (fresh_warm, fresh_forced, selected, persistent_selected,
                       persistent_warm, persistent_forced):
            self.assertTrue(report["summary"]["complete"], report)
            outcomes = self.outcomes(report)
            self.assertEqual(outcomes.keys(), baseline.keys())
            for mutant_id, outcome in baseline.items():
                selected_outcome = outcomes[mutant_id]
                if selected_outcome == "no-coverage":
                    self.assertEqual(outcome, "survived", mutant_id)
                else:
                    self.assertEqual(selected_outcome, outcome, mutant_id)
        self.assertGreater(fresh_warm["cache"]["outcomeHits"], 0, fresh_warm["cache"])
        self.assertEqual(fresh_forced["cache"]["outcomeHits"], 0, fresh_forced["cache"])
        self.assertGreater(persistent_warm["cache"]["outcomeHits"], 0, persistent_warm["cache"])
        self.assertEqual(persistent_forced["cache"]["outcomeHits"], 0,
                         persistent_forced["cache"])

        # An inventory change invalidates selection outcomes and the baseline
        # coverage used to justify them.
        extra_test = root / "test" / "cache-inventory.test.ts"
        extra_test.write_text("import { it, expect } from 'vitest';\nit('inventory addition', () => expect(true).toBe(true));\n")
        changed_inventory = run(*parallel)
        self.assertEqual(changed_inventory["baseline"]["outcome"], "passed")
        self.assertEqual(changed_inventory["cache"]["outcomeHits"], 0,
                         changed_inventory["cache"])

    def test_requested_mutator_families_have_separate_outcome_caches(self):
        first = self.run_oximut('--mutators', 'boolean-literal')
        self.assertEqual(first['summary']['total'], 2)
        self.assertEqual(first['cache']['outcomeHits'], 0)
        warm = self.run_oximut('--mutators', 'boolean-literal')
        self.assertEqual(warm['cache']['outcomeHits'], 2)
        boundary = self.run_oximut('--mutators', 'comparison-boundary')
        self.assertEqual(boundary['summary']['total'], 1)
        self.assertEqual(boundary['cache']['outcomeHits'], 0)
        self.assertNotEqual(first['cache']['fingerprint'], boundary['cache']['fingerprint'])
        boundary_warm = self.run_oximut('--mutators', 'comparison-boundary')
        self.assertEqual(boundary_warm['cache']['outcomeHits'], 1)
        original_scope = self.run_oximut('--mutators', 'boolean-literal')
        self.assertEqual(original_scope['cache']['outcomeHits'], 2)
        self.assertEqual(self.outcomes(first), self.outcomes(original_scope))

    def test_warm_cache_reuses_mutants_but_always_runs_baseline_and_force_agrees(self):
        first = self.run_oximut("--test-selection")
        calls_after_first = len(self.read_calls())
        self.assertGreater(calls_after_first, 1, self.read_calls())
        self.assertTrue(first["summary"]["complete"], first)
        self.assertEqual(len(first["cache"]["fingerprintProfiles"]), 2,
                         first["cache"]["fingerprintProfiles"])

        second = self.run_oximut("--test-selection")
        self.assertEqual(len(second["cache"]["fingerprintProfiles"]), 2,
                         second["cache"]["fingerprintProfiles"])
        self.assertEqual(second["baseline"]["outcome"], "passed")
        self.assertEqual(self.read_calls().count("baseline:baseline"), 2,
                         "baseline validation must run for each invocation")
        self.assertEqual(self.outcomes(first), self.outcomes(second))
        self.assertGreater(second["cache"]["outcomeHits"], 0, second["cache"])
        self.assertGreater(second["cache"]["instrumentationHits"], 0, second["cache"])
        self.assertTrue(any(m.get("provenance", {}).get("source") == "cache"
                            for m in second["mutants"]), second["mutants"])

        forced = self.run_oximut("--test-selection", "--force")
        self.assertEqual(self.outcomes(first), self.outcomes(forced))
        self.assertEqual(forced["cache"]["outcomeHits"], 0, forced["cache"])
        self.assertEqual(forced["cache"]["instrumentationHits"], 0, forced["cache"])

    def test_baseline_input_change_retries_once_with_fresh_inventory(self):
        initial = self.run_oximut()
        initial_ids = set(self.outcomes(initial))
        added_source = (
            self.root / "src/subject.ts"
        ).read_text() + "\nexport function added(value: number): boolean { return value === 0; }\n"
        self.control.write_text(
            f"edit-baseline-once|src/subject.ts|{added_source}"
        )
        calls_before = len(self.read_calls())
        retried = self.run_oximut()
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 2, current_calls)
        self.assertEqual(retried["baseline"]["outcome"], "passed", retried)
        self.assertEqual(retried["cache"]["outcomeHits"], 0, retried["cache"])
        self.assertTrue(retried["summary"]["complete"], retried)
        self.assertTrue(all(m.get("provenance", {}).get("source") != "cache"
                            for m in retried["mutants"]), retried["mutants"])
        self.assertNotEqual(set(self.outcomes(retried)), initial_ids,
                            "retry must regenerate mutants from the changed source")

        forced = self.run_oximut("--force")
        self.assertEqual(self.outcomes(retried), self.outcomes(forced))
        self.assertEqual(retried["baseline"]["tests"], forced["baseline"]["tests"])

    def test_declared_input_read_failure_fails_closed_without_retry_or_publication(self):
        declared = self.base / "declared-input.fixture"
        declared.write_text("stable-input")
        first = self.run_oximut("--cache-input", declared)
        warm = self.run_oximut("--cache-input", declared)
        self.assertGreater(warm["cache"]["outcomeHits"], 0, warm["cache"])
        old_records = {path.name: path.read_bytes() for path in
                       (self.root / ".oximut/cache/outcomes").glob("*.json")}
        self.assertTrue(old_records, first)

        self.control.write_text(f"delete-baseline-input-once|{declared}")
        calls_before = len(self.read_calls())
        report = self.run_oximut("--cache-input", declared, success=False)
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 1, current_calls)
        self.assertFalse(report["summary"]["complete"], report)
        self.assertFalse(report["cache"]["fingerprintProfiles"][-1]["completed"],
                         report["cache"]["fingerprintProfiles"])
        self.assertEqual(old_records, {path.name: path.read_bytes() for path in
                                       (self.root / ".oximut/cache/outcomes").glob("*.json")})

    def test_second_baseline_input_change_fails_closed_without_third_attempt(self):
        self.run_oximut()
        old_records = {path.name: path.read_bytes() for path in
                       (self.root / ".oximut/cache/outcomes").glob("*.json")}
        self.assertTrue(old_records)
        source = (self.root / "src/subject.ts").read_text()
        first = source + "\n// first race edit\n"
        second = source + "\n// second race edit\n"
        self.control.write_text(
            f"edit-baseline-twice|src/subject.ts|{first}|{second}"
        )
        calls_before = len(self.read_calls())
        report = self.run_oximut(success=False)
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 2, current_calls)
        self.assertFalse(report["summary"]["complete"], report)
        self.assertEqual(report["cache"]["outcomeHits"], 0, report["cache"])
        self.assertTrue(all(m.get("provenance", {}).get("source") != "cache"
                            for m in report["mutants"]), report["mutants"])
        self.assertEqual(old_records, {path.name: path.read_bytes() for path in
                                       (self.root / ".oximut/cache/outcomes").glob("*.json")})

    def test_persistent_shutdown_failure_prevents_retry_and_publication(self):
        self.adapter.write_text(FAKE_PERSISTENT_ADAPTER)
        args = ("--execution", "persistent")
        self.control.write_text("")
        base_env = {"OXIMUT_PERSISTENT_ROOT": str(self.root),
                    "OXIMUT_PERSISTENT_CONTROL": str(self.control)}
        self.run_oximut(*args, env=base_env)
        old_records = {path.name: path.read_bytes() for path in
                       (self.root / ".oximut/cache/outcomes").glob("*.json")}
        self.assertTrue(old_records)

        self.control.write_text("shutdown-fail")
        calls_before = len(self.read_calls())
        report = self.run_oximut(*args, env=base_env, success=False)
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 1, current_calls)
        self.assertFalse(report["summary"]["complete"], report)
        self.assertFalse(any("retrying once" in d for d in report["diagnostics"]),
                         report["diagnostics"])
        self.assertEqual(old_records, {path.name: path.read_bytes() for path in
                                       (self.root / ".oximut/cache/outcomes").glob("*.json")})

    def test_persistent_shutdown_edit_runs_one_fresh_retry_after_cleanup(self):
        self.adapter.write_text(FAKE_PERSISTENT_ADAPTER)
        args = ("--execution", "persistent")
        self.control.write_text("")
        base_env = {"OXIMUT_PERSISTENT_ROOT": str(self.root),
                    "OXIMUT_PERSISTENT_CONTROL": str(self.control)}
        initial = self.run_oximut(*args, env=base_env)
        initial_ids = set(self.outcomes(initial))
        added_source = (
            self.root / "src/subject.ts"
        ).read_text() + "\nexport function added(value: number): boolean { return value === 0; }\n"
        self.control.write_text(f"edit-on-stop|src/subject.ts|{added_source}")
        calls_before = len(self.read_calls())
        retried = self.run_oximut(*args, env=base_env)
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 2, current_calls)
        self.assertTrue(any("retrying once" in d for d in retried["diagnostics"]),
                        retried["diagnostics"])
        self.assertTrue(retried["summary"]["complete"], retried)
        self.assertEqual(retried["cache"]["outcomeHits"], 0, retried["cache"])
        self.assertTrue(all(m.get("provenance", {}).get("source") != "cache"
                            for m in retried["mutants"]), retried["mutants"])
        self.assertNotEqual(set(self.outcomes(retried)), initial_ids,
                            "post-cleanup retry must regenerate the changed mutant inventory")

        forced = self.run_oximut(*args, "--force", env=base_env)
        self.assertEqual(self.outcomes(retried), self.outcomes(forced))

    def test_project_and_declared_inputs_invalidate_warm_entries(self):
        # These are all part of the project-wide execution fingerprint. Each
        # edit must force fresh outcomes for the whole project.
        tracked = [
            self.root / "src/subject.ts",
            self.root / "test/subject.test.ts",
            self.root / "vitest.config.ts",
            self.root / "test/setup.ts",
            self.root / "test/example.snap",
            self.root / "package-lock.json",
        ]
        (self.root / "test/setup.ts").write_text("export const setupVersion = 1;\n")
        (self.root / "test/example.snap").write_text("snapshot-v1\n")
        (self.root / "package-lock.json").write_text('{"lockfileVersion":1}\n')
        self.run_oximut()
        for index, path in enumerate(tracked):
            with self.subTest(input=path.name):
                path.write_text(path.read_text() + f"// change {index}\n")
                report = self.run_oximut()
                self.assertEqual(report["cache"]["outcomeHits"], 0, report["cache"])
                self.assertEqual(report["cache"]["outcomeMisses"], len(report["mutants"]),
                                 report["cache"])
                warm = self.run_oximut()
                self.assertGreater(warm["cache"]["outcomeHits"], 0, warm["cache"])

        declared_file = self.base / "external.fixture"
        declared_file.write_text("external-v1")
        first = self.run_oximut("--cache-input", declared_file)
        declared_file.write_text("external-v2")
        changed = self.run_oximut("--cache-input", declared_file)
        self.assertEqual(changed["cache"]["outcomeHits"], 0, changed["cache"])
        self.assertEqual(self.outcomes(first), self.outcomes(changed))
        self.assertGreater(self.run_oximut("--cache-input", declared_file)["cache"]["outcomeHits"], 0)

        adapter_v1 = self.adapter.read_text()
        self.adapter.write_text(adapter_v1 + "\n// changed adapter behavior\n")
        changed_adapter = self.run_oximut("--cache-input", declared_file)
        self.assertEqual(changed_adapter["cache"]["outcomeHits"], 0, changed_adapter["cache"])
        self.assertGreater(self.run_oximut("--cache-input", declared_file)["cache"]["outcomeHits"], 0)

        # Runtime dependencies next to the entry point must be fingerprinted,
        # even with the adapter directory outside the project root.
        protocol = self.adapter.parent / "protocol.mjs"
        protocol.write_bytes((REPO / "packages/vitest-adapter/protocol.mjs").read_bytes())
        self.run_oximut("--cache-input", declared_file)
        self.assertGreater(self.run_oximut("--cache-input", declared_file)["cache"]["outcomeHits"], 0)
        protocol.write_text(protocol.read_text() + "\n// changed protocol implementation\n")
        changed_protocol = self.run_oximut("--cache-input", declared_file)
        self.assertEqual(changed_protocol["cache"]["outcomeHits"], 0, changed_protocol["cache"])
        self.assertGreater(self.run_oximut("--cache-input", declared_file)["cache"]["outcomeHits"], 0)

        with_env = self.run_oximut("--cache-input", declared_file, "--cache-env", "OXIMUT_FIXTURE_MODE",
                                   env={"OXIMUT_FIXTURE_MODE": "alpha"})
        changed_env = self.run_oximut("--cache-input", declared_file, "--cache-env", "OXIMUT_FIXTURE_MODE",
                                      env={"OXIMUT_FIXTURE_MODE": "beta"})
        self.assertEqual(changed_env["cache"]["outcomeHits"], 0, changed_env["cache"])
        self.assertEqual(self.outcomes(with_env), self.outcomes(changed_env))
        persisted = b"".join(path.read_bytes() for path in
                              (self.root / ".oximut/cache").rglob("*.json"))
        self.assertNotIn(b"alpha", persisted)
        self.assertNotIn(b"beta", persisted)

    def test_auto_policy_bypasses_expensive_history_and_always_overrides_it(self):
        prime = self.run_oximut(cache_policy="auto")
        self.assertTrue(prime["summary"]["complete"], prime)
        self.tamper_policy_history(fingerprintCostMs=1_000_000,
                                  freshWorkMs=1, successfulBypasses=0,
                                  bypassOutcomes=True)
        calls_before = len(self.read_calls())
        auto = self.run_oximut(cache_policy="auto")
        current_calls = self.read_calls()[calls_before:]
        self.assertEqual(current_calls.count("baseline:baseline"), 1, current_calls)
        self.assertEqual(auto["cache"]["reason"],
                         "outcome-cache-cost-exceeds-fresh-work", auto["cache"])
        self.assertFalse(auto["cache"]["outcomesEnabled"], auto["cache"])
        self.assertEqual(auto["cache"]["outcomeHits"], 0, auto["cache"])
        self.assertIsNone(auto["cache"].get("fingerprint"), auto["cache"])
        self.assertEqual(auto["cache"]["fingerprintProfiles"], [], auto["cache"])
        self.assertTrue(all(m.get("provenance", {}).get("source") != "cache"
                            for m in auto["mutants"]), auto["mutants"])
        forced = self.run_oximut("--force", cache_policy="always")
        self.assertEqual(self.outcomes(auto), self.outcomes(forced))

        always = self.run_oximut(cache_policy="always")
        self.assertGreater(always["cache"]["outcomeHits"], 0, always["cache"])
        self.assertEqual(len(always["cache"]["fingerprintProfiles"]), 2,
                         always["cache"]["fingerprintProfiles"])
        self.assertEqual(self.outcomes(prime), self.outcomes(always))

    def test_auto_policy_corrupt_history_falls_back_to_full_validation(self):
        prime = self.run_oximut(cache_policy="auto")
        history = self.policy_history_files()[0]
        history.write_text('{"schemaVersion":999}\n')
        report = self.run_oximut(cache_policy="auto")
        self.assertTrue(report["summary"]["complete"], report)
        self.assertEqual(len(report["cache"]["fingerprintProfiles"]), 2,
                         report["cache"]["fingerprintProfiles"])
        self.assertEqual(report["cache"]["reason"], "outcome-cache-cost-probe",
                         report["cache"])
        self.assertEqual(report["cache"]["outcomeHits"], 0, report["cache"])
        self.assertTrue(all(m.get("provenance", {}).get("source") != "cache"
                            for m in report["mutants"]), report["mutants"])
        self.assertEqual(self.outcomes(prime), self.outcomes(report))

    def test_auto_policy_source_change_uses_new_history_key_and_reprobes(self):
        self.run_oximut(cache_policy="auto")
        old_history = {path.name for path in self.policy_history_files()}
        source = self.root / "src/subject.ts"
        source.write_bytes(source.read_bytes().replace(b"< 10", b"< 11", 1))
        report = self.run_oximut(cache_policy="auto")
        self.assertEqual(report["cache"]["outcomeHits"], 0, report["cache"])
        self.assertEqual(len(report["cache"]["fingerprintProfiles"]), 2,
                         report["cache"]["fingerprintProfiles"])
        self.assertTrue(set(path.name for path in self.policy_history_files()) - old_history,
                        self.policy_history_files())

    def test_auto_policy_reprobes_after_eight_successful_bypasses(self):
        self.run_oximut(cache_policy="auto")
        self.tamper_policy_history(fingerprintCostMs=1_000_000,
                                  freshWorkMs=1, successfulBypasses=8,
                                  bypassOutcomes=True)
        calls_before = len(self.read_calls())
        report = self.run_oximut(cache_policy="auto")
        self.assertEqual(self.read_calls()[calls_before:].count("baseline:baseline"), 1)
        self.assertEqual(report["cache"]["reason"], "outcome-cache-cost-probe",
                         report["cache"])
        self.assertEqual(report["cache"]["outcomeHits"], 0, report["cache"])
        self.assertGreaterEqual(len(report["cache"]["fingerprintProfiles"]), 2,
                                report["cache"]["fingerprintProfiles"])

    def test_auto_policy_does_not_publish_history_for_failed_baseline_or_cleanup(self):
        self.adapter.write_text(FAKE_PERSISTENT_ADAPTER)
        self.control.write_text("")
        env = {"OXIMUT_PERSISTENT_ROOT": str(self.root),
               "OXIMUT_PERSISTENT_CONTROL": str(self.control)}
        args = ("--execution", "persistent")
        initial = self.run_oximut(*args, env=env, cache_policy="auto")
        self.assertTrue(initial["summary"]["complete"], initial)
        old_history = {path.name: path.read_bytes() for path in self.policy_history_files()}
        self.assertTrue(old_history)

        self.control.write_text("baseline-fail-once")
        baseline_failed = self.run_oximut(*args, env=env, cache_policy="auto", success=False)
        self.assertFalse(baseline_failed["summary"]["complete"], baseline_failed)
        self.assertEqual(old_history, {path.name: path.read_bytes()
                                      for path in self.policy_history_files()})

        self.control.write_text("shutdown-fail")
        cleanup_failed = self.run_oximut(*args, env=env, cache_policy="auto", success=False)
        self.assertFalse(cleanup_failed["summary"]["complete"], cleanup_failed)
        self.assertEqual(old_history, {path.name: path.read_bytes()
                                      for path in self.policy_history_files()})

    def test_content_mode_and_inherited_environment_changes_invalidate_then_warm(self):
        source = self.root / "src/subject.ts"
        env_name = "OXIMUT_M4_INHERITED_CACHE_FIXTURE"
        env = {env_name: "first"}
        original_bytes = source.read_bytes()
        original_stat = source.stat()
        self.run_oximut(env=env)
        warm = self.run_oximut(env=env)
        self.assertGreater(warm["cache"]["outcomeHits"], 0, warm["cache"])

        # A metadata-only fingerprint would miss this equal-length byte edit.
        changed_bytes = original_bytes.replace(b"< 10", b"< 11", 1)
        self.assertEqual(len(changed_bytes), len(original_bytes))
        self.assertNotEqual(changed_bytes, original_bytes)
        source.write_bytes(changed_bytes)
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        changed_content = self.run_oximut(env=env)
        self.assertEqual(changed_content["cache"]["outcomeHits"], 0,
                         changed_content["cache"])
        self.assertGreater(self.run_oximut(env=env)["cache"]["outcomeHits"], 0)

        # Unix mode bits affect executable behavior and are part of the input tree.
        mode = source.stat().st_mode
        source.chmod(mode ^ 0o100)
        changed_mode = self.run_oximut(env=env)
        self.assertEqual(changed_mode["cache"]["outcomeHits"], 0,
                         changed_mode["cache"])
        self.assertGreater(self.run_oximut(env=env)["cache"]["outcomeHits"], 0)

        # Inherited environment is part of the fingerprint even without --cache-env.
        changed_env = self.run_oximut(env={env_name: "second"})
        self.assertEqual(changed_env["cache"]["outcomeHits"], 0,
                         changed_env["cache"])
        stable_env = self.run_oximut(env={env_name: "second"})
        self.assertGreater(stable_env["cache"]["outcomeHits"], 0,
                           stable_env["cache"])

    def test_incomplete_or_undeclared_inputs_never_publish_reusable_outcomes(self):
        no_assertion = subprocess.run(
            [str(BINARY), "--root", str(self.root), "--config", "vitest.config.ts",
             "--source", "src/subject.ts", "--adapter", str(self.adapter), "--report", str(self.report_path),
             "--cache"], cwd=REPO, text=True, capture_output=True, timeout=60,
            env={**os.environ, "OXIMUT_CACHE_CALLS": str(self.calls)},
        )
        self.assertIn(no_assertion.returncode, (0, 1), no_assertion.stdout + no_assertion.stderr)
        first = json.loads(self.report_path.read_text())
        self.assertFalse(first["cache"]["outcomesEnabled"], first["cache"])
        self.assertEqual(first["cache"]["reason"], "external-inputs-not-asserted-complete")
        call_count = len(self.read_calls())
        second = subprocess.run(
            [str(BINARY), "--root", str(self.root), "--config", "vitest.config.ts",
             "--source", "src/subject.ts", "--adapter", str(self.adapter), "--report", str(self.report_path),
             "--cache"], cwd=REPO, text=True, capture_output=True, timeout=60,
            env={**os.environ, "OXIMUT_CACHE_CALLS": str(self.calls)},
        )
        self.assertIn(second.returncode, (0, 1), second.stdout + second.stderr)
        self.assertGreater(len(self.read_calls()), call_count, "undeclared inputs must disable outcome reuse")

        # A partial result cannot become a warm success. The failed pass uses a
        # separate cache tree so a preexisting complete record cannot mask it.
        previous = self.run_oximut()
        old_records = {path.name: path.read_bytes() for path in
                       (self.root / ".oximut/cache/outcomes").glob("*.json")}
        self.assertTrue(old_records, previous)
        self.control.write_text("fail-baseline-once")
        failed_baseline = self.run_oximut(success=False)
        self.assertEqual(failed_baseline["baseline"]["outcome"], "failed", failed_baseline)
        self.assertEqual(old_records, {path.name: path.read_bytes() for path in
                                       (self.root / ".oximut/cache/outcomes").glob("*.json")})
        self.control.write_text("fail-mutants-once")
        failed = self.run_oximut("--order-seed", "74", success=False)
        self.assertFalse(failed["summary"]["complete"], failed)
        self.assertEqual(old_records, {path.name: path.read_bytes() for path in
                                       (self.root / ".oximut/cache/outcomes").glob("*.json")})
        (self.calls).unlink(missing_ok=True)
        recovered = self.run_oximut()
        self.assertGreater(recovered["cache"]["outcomeHits"], 0, recovered["cache"])
        self.assertEqual(self.outcomes(previous), self.outcomes(recovered))

    def test_node_runtime_identity_probe_timeout_disables_outcomes_and_reaps_process(self):
        shim_dir = self.base / "node-shim"
        shim_dir.mkdir()
        pid_file = self.base / "runtime-probe.pid"
        child_pid_file = self.base / "runtime-probe-child.pid"
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node must be available on PATH")
        node = Path(node)
        self.assertTrue(node.is_file(), node)
        shim = shim_dir / "node"
        shim.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "--eval" ]; then\n'
            f"  echo $$ > {shlex.quote(str(pid_file))}\n"
            f"  sleep 30 & echo $! > {shlex.quote(str(child_pid_file))}\n"
            "  wait\n"
            "else\n"
            f"  exec {shlex.quote(str(node))} \"$@\"\n"
            "fi\n"
        )
        shim.chmod(0o755)
        search_path = os.pathsep.join(
            [str(shim_dir), str(node.parent), os.environ.get("PATH", "")]
        )
        report = self.run_oximut(
            "--timeout-ms", "1000", env={"PATH": search_path}
        )
        self.assertEqual(
            report["cache"]["reason"],
            "unsafe-or-incomplete-fingerprint: node runtime identity deadline exceeded",
        )
        self.assertEqual(report["baseline"]["outcome"], "passed", report)
        self.assertTrue(report["summary"]["complete"], report)
        self.assertGreater(len(self.read_calls()), 1, "adapter runs should delegate to real Node")
        self.assertTrue(pid_file.exists(), "runtime identity probe should reach the hanging shim")
        self.assertTrue(child_pid_file.exists(), "runtime identity shim should start its child")
        for pid_path in (pid_file, child_pid_file):
            probe_pid = int(pid_path.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(probe_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail(f"timed out runtime identity process {probe_pid} was not reaped")

    def test_corrupt_cache_entry_is_a_miss_and_force_and_no_cache_bypass_reuse(self):
        self.run_oximut()
        outcome_files = list((self.root / ".oximut/cache/outcomes").glob("*.json"))
        self.assertTrue(outcome_files, "successful run should persist an outcome record")
        outcome_files[0].write_text('{"schemaVersion":999,"complete":true}\n')
        cold = self.run_oximut()
        self.assertEqual(cold["cache"]["outcomeHits"], 0, cold["cache"])

        warm = self.run_oximut()
        temporary_entry = outcome_files[0].with_name(".interrupted-write.tmp")
        temporary_entry.write_text('{"schemaVersion":1')
        after_interruption = self.run_oximut()
        self.assertGreater(after_interruption["cache"]["outcomeHits"], 0,
                           after_interruption["cache"])
        self.assertGreater(warm["cache"]["outcomeHits"], 0, warm["cache"])
        for flag in ("--force", "--no-cache"):
            with self.subTest(flag=flag):
                bypassed = self.run_oximut(flag)
                self.assertEqual(bypassed["cache"]["outcomeHits"], 0, bypassed["cache"])
                self.assertEqual(self.outcomes(warm), self.outcomes(bypassed))


if __name__ == "__main__":
    unittest.main()
