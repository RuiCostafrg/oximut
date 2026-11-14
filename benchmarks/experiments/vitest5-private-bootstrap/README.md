# Identity-safe private bootstrap: stopped on source-map fidelity

This is ONE standalone experimental worker bundle. It preserves native collector
and API identity, but **fails the bootstrap-stack fidelity gate**. It is not a
qualified candidate, product backend or performance result. The earlier
collector-inlining negative control at `328917c` is unchanged and is not reused
as qualification.

The selected modules and source hashes are explicit in `audited-inputs.json`:
`workers/forks.js`, `base.Cof-UHJf.js`, `init-forks.BaDXQSD8.js`,
`index.w_oBZ9N8.js`, `coverage.9iqXE2gN.js`, and `setup-common.CqAQCXzx.js`.
The recomputed worker/public-API intersection (all 15 files, including
`run.CjdgFZiW.js`, `index.CZzC1gGg.js`, spy, source-map and snapshot owners)
stays native. So do mocks, module evaluator, environment/provider loaders,
native module loaders and every uncertain private module.

`build.mjs` traverses literal imports, exports and lazy imports using Node's
original importer URL, under `--experimental-import-meta-resolve`. It checks
the pinned source hashes, the complete worker graph, the intersection, and the
absence of native-to-inline ingress. The resolution test uses two distinct
importer directories with conditional package exports to catch resolution
relative to this script or require conditions instead of the original importer.
This is a conservative audit of the declared Node-only experiment, not a proof
about computed imports inside native modules or every plugin/custom loader.

Only one metadata form is supported in inline source: the audited base module's
`import.meta.url`, replaced syntactically with its original URL and a precise
source map. Unknown metadata, context identifiers, computed imports, import
attributes/options and unaudited inline modules reject. Literal lazy targets
remain native absolute URLs and lazy. Node builtins and all external packages
stay native, using import-condition resolution. The AST parser rejects unknown
grammar. Audited source hashes reject new source even if its syntax parses.
The bundler's known virtual runtime is retained as actual source plus hash.

For an explicit future reproduction (the failure is expected; do not retry it
to obtain a green result):

```sh
npm ci --ignore-scripts --no-audit --no-fund
node --experimental-import-meta-resolve --test benchmarks/experiments/vitest5-private-bootstrap/test-probe.mjs
node --experimental-import-meta-resolve benchmarks/experiments/vitest5-private-bootstrap/probe.mjs /tmp/NEW-private-matrix
node --experimental-import-meta-resolve benchmarks/experiments/vitest5-private-bootstrap/framework-stack.mjs /tmp/NEW-private-matrix /tmp/NEW-bootstrap-stack
```

Both captures are required. A main-matrix pass alone is **not qualification**.
The second gate reuses the first capture's exact bundle/map hashes, without
rebuilding or repairing the candidate. It exercises a stack originating in
the bundled base module, in addition to the matrix's project TypeScript stacks.
Any invariant failure stops execution; raw results are written before report
assertions. Each output directory must be new.

The main matrix uses actual import-time selectors and exact expected outcomes:
inactive passes both arithmetic tests, A fails add only, B fails multiply only,
then A fails add only again. All five test identities remain fixed. Each job
uses a fresh real `ForksPoolWorker`, a distinct PID/birth identity, a setup realm
UUID, `isolate: true`, and explicit pool drain before the next admission.
The fixture dirties local/module state, globals, environment, a builtin function
property and fake clocks. Native CJS/ESM external counters, hoisted mocks,
spies, setup/hook counts, real late timers, mapped TypeScript stacks, test throws
and import throws are checked against stock. Sources are hashed before/after.

Deadline fixtures spawn an actual descendant then synchronously hang. A
dedicated controller process group is killed at five seconds. PID/PPID/group/
birth ticks are retained, and the group must become empty, including zombies,
within bounded cleanup. Normal controllers must leave no live descendants;
unexpected descendants trigger cleanup and structural failure. This controlled
group oracle does not establish cleanup of arbitrary daemonized descendants
that escape their group or production deadline integration.

The capture records complete installed-file hashes, Node executable hash,
source snapshots/manifests, resolved imports, source edits, artifact/map hashes,
stdout/stderr/statuses, job results, events and cleanup. Original installation
URLs bind the artifact to its checkout; it is not a relocatable release bundle.
Only unused, explicitly identified optional lazy environment/tracing packages
may be absent; missing static or unknown lazy dependencies reject.

See the retained result under
`benchmarks/results/2026-11-14-w13-private-bootstrap/REPORT.md`. No native CI
was dispatched because the full local gate failed. No profilers, scored runs,
product integration, dependency changes or default-isolation changes occurred.
