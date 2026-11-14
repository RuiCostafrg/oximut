# W13 scoped map registration: stop on full source URI fidelity

Base `70ad922063071dc85c9e3604e6859f84cf7ff2b7`, branch
`perf/vitest5-worker-bundle`. All previous commits and failure artifacts remain
unchanged. This revision is GPT-6.1 Sol work. No action was taken on PR67, W11
or main; main remains unsettled.

## Decision

One new candidate/artifact was built with scoped map registration. The complete
fixture matrix initially passed its basename/line/column checks, but a full URI
audit immediately afterward found a substantive mapping error. **Stop this
candidate: it is not qualified.** The helper/artifact was not repaired or rerun
after this discovery. Native CI was not dispatched and nothing was scored.

Stock's exact bootstrap frame is:

```text
at process.exit (file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/base.Cof-UHJf.js:123:9)
```

The new candidate reports:

```text
at process.exit (file:///root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/file:/root/.t3/worktrees/oximut/t3-8d8fd2c8-76fe618371a29e13/node_modules/vitest/dist/chunks/base.Cof-UHJf.js:123:9)
```

The source URI has an extra worktree/`file:` component. Matching only the suffix
`base.Cof-UHJf.js:123:9` was insufficient. The preserved `local/capture.json` and
`local/run/evidence.json` retain their historical green labels; **this report,
`outcome.json` and `uri-audit/` supersede those labels**. The final oracle now
compares the full original bootstrap URI and position. The real Vite mapping
focused test also requires its full original URI and correctly fails. No stack
text was edited to satisfy an assertion.

## Investigation and attempted fix

Pinned Vitest 5.1.0 creates Vite runners with
`sourcemapInterceptor: "prepareStackTrace"` in `init.CO4AB7HZ.js:26` and
`index.Bi0NXYPf.js:605`. Vite 8.3.5's `module-runner.js:929-1018` owns its own
runner graphs, retrieval handlers, decoded-map and file-content caches.
Without a retrieval handler it cannot read this native worker bundle's map
from disk, and it can cache a null lookup. Its prepareStackTrace implementation
is at 1097; native Node source-map support is a separate branch at 1107.
The inspected runtime has Node source-map support disabled. Node exposes
findSourceMap/SourceMap and process-wide support switches, but the chosen Vite
interceptor does not automatically consume those native caches. No Node flag
or process-wide source-map switch was enabled.

The small attempted fix adds an external `map-registration.mjs` helper through
an emitted bundle banner. It creates one native Vite ModuleRunner with an empty
non-fetching graph, using the public `retrieveSourceMap` option. Registration
precedes bootstrap evaluation. Only the exact bundle path/URL can retrieve the
map. Metadata binds map hash, six original source URLs/content hashes and the
actual bundle URL. Missing maps reject on registration; parsing, hashing and
source validation are lazy and occur once on first retrieval. Unknown metadata,
unsupported map structure and corrupt source/map bytes reject. Literal lazy
framework imports remain native/lazy. The six-module allowlist and complete
15-module native shared/API intersection are unchanged. APIs, mocks, evaluator
and collector remain native; no Error.stack replacement was implemented.

The failure is in this adapter's map-base representation: it returns a file URL
as the map's `url`, while pinned Vite's DecodedMap constructor at lines 258-264
uses `posixDirname(from)` and `posixResolve(originDir, source)`. That path-oriented
consumer treats the URL's `file:` component as part of a filesystem path;
subsequent frame formatting exposes the malformed source URI. This is not a
claim that all registration approaches or all bundling strategies are unsound.
No alternate base representation was tried after discovery.

Hook close removes this owner's handler without removing other runner hooks.
Vite's private decoded-map/file-content caches cannot be cleared through that
API and persist until fresh process exit. Repeat registration of the same
bundle URL rejects. There is no warm worker or reuse claim.

## Node provenance correction

The first report mislabeled a distribution archive hash as an executable hash.
The current capture verifies the following chain, rather than assuming that a
path/version implies the same binary:

- Executed path: `/tmp/oximut-v5-node/node-v26.3.1-linux-arm64/bin/node`.
  It reports Node v26.3.1, Linux arm64, on this Android/PRoot host.
- Archive: `https://nodejs.org/dist/v26.3.1/node-v26.3.1-linux-arm64.tar.xz`.
  Its SHA-256 is `c021380e64d1314d1218ab1f31e0f5b0f28f1f54ac779ef72a16c2bda0ca5c30`.
- Fresh HTTPS upstream SHASUMS256 bytes are retained in
  `local/upstream-SHASUMS256.txt`. The existing archive matches that checksum.
- The archive's streamed `bin/node` member hashes to
  `a76224241ff1ebcbf90adc2eb4600ddb048d19f883c6a9140b6c3ceab004e4f0`.
  The actual executable independently hashes to that same value.

Both older raw capture JSON files already recorded executable hash `a762…4f0`
(`nodeBinarySha256` in the negative capture and `node.sha256` in the previous
private capture). Their raw executable hashes agree with the newly verified
binary. The inconsistent prose `c021…c30` described the archive, not another
executable. Old artifacts/prose were preserved; this is the explicit correction.
`local/runtime-provenance.json` records all URLs, hashes, path and version.
This is checksum verification over HTTPS, not a claim of signature verification.

## Completed gates, failure and extra costs

The new artifact is SHA-256
`e10d18c759eab4f36dbe8b9a38df5ee16b898695e25be7d79f8229f679c21de8`;
map SHA-256 is
`ae540a9648c54acb226a08e92063a30ace9623cfa48126f2e6b9f1f9d40470a3`.
Registration/helper hashes and installed source/tool hashes are bound in the
capture. Versions remain Node 26.3.1, Vitest/coverage-v8 5.1.0, Vite 8.3.5,
Rolldown 1.2.15, Babel parser 8.0.10 and magic-string 1.4.5.

The single unified matrix uses this same new artifact for every candidate job.
It runs real inactive/A/B/A arithmetic outcomes and exact inventory, fresh
distinct PIDs/realms, module/global/env/builtin/fake-clock contamination,
native CJS/ESM external state, mocks/spies/setup/hooks, late real timers, project
mapped stacks, bootstrap basename/line/column stacks, test/import throws and
hard deadlines with observed descendants. All 16 job PIDs are distinct; both
deadline groups and normal groups become entirely empty, including zombies.
These gates passed, but do not override the full original-URI failure.

Ten focused tests initially passed, including real Vite mapping, lazy lookup,
scoped cleanup, missing/unsupported/corrupt maps and failed-validation caching.
The original-URI audit then rejected the captured candidate stack without
executing the candidate again. Strengthening the scoped real-mapping test
produced 3 passes / 1 failure, specifically the original URI. Both commands'
complete stdout/stderr/statuses are in `uri-audit/`. Current capture commands
will reject that known failure rather than silently print a qualification pass.

Diagnostics retain eight registrations and one actual map load. Registration
reads 1,715 metadata bytes and stats the map per fresh worker; actual map bytes
are not read until retrieval. The one map load reads 39,009 bytes, verifies its
hash and six source-content hashes, and parses JSON. Registration callbacks
total 25,756 microseconds user+system CPU across eight workers; the first map
callback records 4,286 microseconds. These are local instrumented callback
diagnostics, not a stock/candidate resource comparison. Native Vite decoding,
cache construction and event logging costs are excluded. Heap deltas are raw
diagnostics affected by GC (including a negative delta); they are not attributable
retained memory or peak RSS. The additional owner graph, parsed map/source
strings, native Vite caches and event I/O have real unscored costs. No startup
saving is assumed; tree CPU/RSS, ratios and confidence intervals are unmeasured.

## Evidence and next gate

`local/` preserves the new bundle/map/metadata, source tar before prerequisites,
all stdout/stderr/statuses, runtime verification, exact source/fixture/tool hashes,
raw events/outcomes and cleanup. The dependency manifest is gzip-compressed;
both compressed and uncompressed SHA-256 are recorded, avoiding another bulky
plaintext inventory. `uri-audit/` preserves the later stricter oracle sources
and failure logs, distinguishing them from the initially weaker capture.
`source-provenance.json` binds final sources. `SHA256SUMS` binds retained files.
The prepared native workflow draft is in the original source tar but was never
dispatched and is not added as an active workflow in this stopped revision.

No native run IDs/URLs: complete local qualification failed. The next justified
gate is a separately authorized new map-base adapter candidate with full
source-URI fidelity checked before any native dispatch. This revision stops
without trying that repair. Native Ubuntu structural qualification, general
coverage/custom loader/escaping-descendant semantics, product/colord equivalence
and all scored performance gates remain incomplete. Dependencies, lockfiles,
product defaults, W11 and main are untouched. No profilers or Rust builds ran.
