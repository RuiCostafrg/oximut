# W13 private-bootstrap candidate: structural failure

Continue from `328917cb2bee82b2059d91fdcb12f7f723fb0f0a` on
`perf/vitest5-worker-bundle`. That commit and its negative-control evidence
remain byte unchanged. This candidate and its investigation are GPT-6.1 Sol
work. Main remains unsettled. No action was taken on PR67, W11 or main.

## Outcome and stop

One identity-safe private-bootstrap candidate was built. Its collector/API
identity checks and main local matrix passed. The additional bootstrap-stack
gate then failed on the SAME artifact, at the first candidate job. Stop:
**this candidate is not structurally qualified**. It was not repaired or
rerun after that failure. No native CI or scored campaign was dispatched.

The original `process.exit` guard in stock reports:

```text
at process.exit (.../vitest/dist/chunks/base.Cof-UHJf.js:123:9)
```

The same guard in the candidate reports:

```text
at process.exit (file:///tmp/oximut-w13-private-attempt-1/run/private-worker.mjs:374:9)
```

Both throw the expected `process.exit unexpectedly called with "7"` error,
but the candidate loses the original bootstrap frame. Generating a `.map`
does not by itself make the pinned worker's stack machinery consume it.
The candidate's bootstrap-stack test fails its original-file/line assertion;
stock passes. `bootstrap-stack/run/events.jsonl` retains both original stacks,
and `bootstrap-stack/run/candidate/result.json` retains the exact test failure.
This is a bounded failure of stack fidelity in this candidate, not renewed
collector divergence or a claim that all bundling is impossible.

## Boundary and quantitative coverage

The independently recomputed literal internal ESM worker graph has 38 modules
and 1,123,860 source bytes. Its complete intersection with the public API has
15 modules and 684,847 bytes; all remain native. The explicit private allowlist
has 6 modules and 24,939 source bytes (2.219% of those worker source bytes).
The remaining 32 modules / 1,098,921 bytes stay native. These counts include
literal lazy paths, not external package closures or actual startup execution;
they are neither speed estimates nor a performance bound.

The allowlist comprises fork entry/IPC, base bootstrap, runner construction,
common setup and a coverage-delegation helper. It has real bootstrap work to
test, so the experiment did not classify it as only an entry wrapper. Heavy
API, runner/collector, evaluator, mocks, environment/provider and native-loader
work remains native. Original-importer conditional-export tests pass. The
whole audited shared intersection is excluded, and no audited native module
has a literal edge into the allowlist, avoiding a second native copy of a
selected module. Uncertain private modules stay external.

The artifact is bound to these sources/tools:

- Node 26.3.1 Linux arm64, executable SHA-256
  `a76224241ff1ebcbf90adc2eb4600ddb048d19f883c6a9140b6c3ceab004e4f0`.
- Vitest and coverage-v8 5.1.0, Vite 8.3.5.
- Rolldown 1.2.15, Babel parser 8.0.10, magic-string 1.4.5, already locked.
- Worker SHA-256 `ee86b87c433e42940c5d64bd47acc3d67b14ef5a6b0a6195a7940b9527e9c23b`.
- Map SHA-256 `bf4d271037ba8f3eb7d82490e2c244f022b045b2edb81f429e381778c5eab1f1`.

The host is Android/PRoot aarch64 using the previously verified official Linux
Node binary, not a native Ubuntu qualification host. This worktree's dependency
installation is the pinned v5 tree, not the stale root v4 installation.

## Completed and failed gates

Six focused tests pass: exact versions, original importer/ESM export resolution,
unsupported syntax/metadata rejection, precise URL transformation with original
map content, complete native intersection/no ingress, and selector-specific
inventory/outcome rejection controls.

`matrix/run/evidence.json` records the first capture's narrow matrix pass:

- Stock and candidate each execute inactive/A/B/A with five identical tests.
  Inactive passes; A fails only add; B fails only multiply; A again fails only
  add. Mutant failure messages and reporter reason are checked too.
- Each of eight sequence jobs has a distinct fresh PID/realm. Local module,
  global, env, builtin and clock contamination is absent on admission and
  deliberately installed before exit. Native external CJS/ESM counters start
  clean, mocks/spies work, and setup/before/after hook counts reconcile.
- Eight project TypeScript stack observations preserve `mapped.ts:2:9`.
  Both modes match test-throw and import-throw outcomes and test inventory;
  import-time error stacks preserve the original test source line.
- Real late timers do not fire after worker drain. Both synchronous hangs
  reach the outer hard deadline with an observed controller/worker/descendant
  chain. Both groups become completely empty, including zombie identities.
- Initial capture has 14 distinct job PIDs/realm markers, including two hung
  workers. The focused stack gate adds one fresh worker per mode. No workers
  are reused. All normal groups are empty after close as well.

`bootstrap-stack/run/evidence.json` is the superseding failure. The main matrix
pass must never be presented as full qualification. Its archived source predates
the focused gate addition; the later capture's source snapshot contains the
extra gate and exact shared-controller changes. No original artifact or fixture
was edited during either execution. No source-map consumption/registration
repair was attempted after the first failing frame assertion.

## Evidence and limits

`matrix/` and `bootstrap-stack/` retain executed source snapshots, outer command
stdout/stderr/statuses, every controller's stdout/stderr/status/result, immutable
fixtures, job identities, events, topology, exact original importer resolutions,
bundle/map and virtual-runtime source, and installed dependency hashes. The
focused gate references and verifies the original artifact hashes. The capture
source snapshots distinguish executed revisions from later documentation.
`SHA256SUMS` binds every retained file; `source-provenance.json` also binds the
current executable probe/oracle sources and unchanged package/lock files.

The controlled deadline fixture does not daemonize or escape its dedicated
process group; arbitrary escaping descendants remain outside this experiment's
cleanup proof. Computed imports and optional native environment/provider paths
are not rewritten or universally qualified. Coverage is disabled in this
structural matrix. Native Ubuntu, full product/colord equivalence and product
integration remain incomplete. Nothing is scored: wall, tree CPU, aggregate RSS,
resource ratios and confidence intervals are unmeasured. No profilers or Rust
builds were run; dependency versions, lockfiles and product defaults are unchanged.

## Next gate

No run URLs: native CI was deliberately not dispatched after the local failure.
A parent-authorized follow-up could investigate pinned stack-map consumption
for private bootstrap frames, preserving this failure and the current native
singleton boundary. A new complete local qualification must pass before a
bounded native Ubuntu structural workflow is justified. Only after that could
an unprofiled paired product/colord versus Stryker campaign test the unchanged
wall <=0.50x and CPU/peak aggregate RSS <=0.80x targets with equivalent mutants.
There is no scored next experiment justified by this capture.
