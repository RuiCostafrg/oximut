# Vitest 5 worker bundle: bounded no-go probe

This is a deliberately invalid **negative control**, not an executable product
candidate. It tests the collector-inlining boundary suggested by PR44 against
Vitest 5.1.0. It never changes default execution, product dependencies or source
fixtures during execution. No warm worker, cache seeding or overlap is used.

Run in a fresh output directory on Node 26.3.1:

```sh
npm ci --ignore-scripts --no-audit --no-fund
node --test benchmarks/experiments/vitest5-worker-bundle-probe/test-probe.mjs
node --experimental-import-meta-resolve benchmarks/experiments/vitest5-worker-bundle-probe/probe.mjs /tmp/NEW-v5-bundle-evidence
```

The exact-version guard also checks Vite 8.3.5, coverage-v8 5.1.0,
Rolldown 1.2.15, Babel parser 8.0.10 and magic-string 1.4.5. The latter three
already exist in the lockfile; no dependency declarations or lockfiles change.
They are diagnostic tooling resolved from this checkout, not product APIs.

The controller constructs the real `ForksPoolWorker`, with `isolate: true`,
`fsModuleCache: false`, and a drained pool between jobs. The stock control
runs inactive/A/B/A with a new PID and UUID realm marker per job and checks
import-time selectors and exact test counts. The negative worker inlines only
`workers/forks.js`, `base.Cof-UHJf.js`, and `run.CjdgFZiW.js`. Every other import
remains a native absolute external URL. This deliberately leaves the public API
with the original collector and tests whether a second collector is safe.

A narrow AST transform preserves actual `import.meta.url` syntax with original
file URLs and source maps containing original sources. Other metadata access,
computed imports, direct require calls and unrecognized inline modules reject.
The one recognized Rolldown virtual runtime is saved as actual source and hashed.
Literal external imports are resolved using Node with its two-argument
`import.meta.resolve` flag. No blanket text replacement or custom test runner
substitutes for Vitest.

`evidence.json` records exact versions, source hashes, transform offsets,
resolved imports, emitted bundle/map hashes, topology, raw results and the
outcome. `installation.json` hashes every installed regular file, including
native tooling, and its hash is bound into evidence. Symlinks in the dependency
installation reject (apart from the excluded executable-link directory).
The Node executable is hashed too. Artifacts retain original installation bases
and cannot be relocated for execution. Installation hashes are provenance,
not a complete semantic analysis of native/external dependencies.

Each controller has a separate Linux process group and a 20 second outer
termination bound. Its stdout/stderr, status, residual group check, worker PIDs
and worker exit statuses are retained. Residual descendants are killed and
reject the probe. The controlled fixture spawns no detached descendants; this
is not a production process-tree cleanup implementation. Broader isolation,
mock/hook, late asynchronous work, import/throw, source-map behavior and deadline
oracles must not be marked passing when the first collector check fails.

The topology audit follows literal import/export/dynamic-import edges within
Vitest only. Missing optional external dependencies are recorded, not installed.
Computed imports and external package closures remain explicit unknowns. Shared
reachability is a warning to audit singleton ownership, not proof that every
shared module contains mutable singleton state. This probe cannot prove all
bundling strategies impossible and does not claim that safe externalization
cannot help. It establishes a bounded stop for collector inlining and identifies
the additional semantic work required for a different boundary.

No resource sampler or profiler is used. Wall, whole-tree CPU, aggregate peak
RSS, ratios and confidence intervals are unmeasured. The original targets
remain wall <=0.50x and CPU/RSS <=0.80x Stryker with equivalent mutants.
There is no structurally qualified candidate to score or integrate.
