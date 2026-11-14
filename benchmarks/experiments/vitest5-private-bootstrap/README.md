# Private bootstrap with scoped map consumption

**Local structural qualification passed; native Ubuntu qualification pending.**
The latest map-base adapter supplies absolute encoded URL pathnames to Vite's
path-oriented decoder and retains the file URL as its protocol base. Full original
URIs, including escaped filenames, now pass. See the new capture in
`benchmarks/results/2026-11-14-w13-map-base/REPORT.md`. Earlier failed artifacts
and their reports, including `2026-11-14-w13-map-registration`, remain unchanged.
No performance qualification or product integration is established.

The revised candidate uses the same six audited inline modules and leaves all
15 worker/public-API intersection modules native. `328917c` and `70ad922`, their
failure artifacts, and the historical reports remain unchanged. See
`benchmarks/results/2026-11-14-w13-private-bootstrap/REPORT.md` for the previous
bootstrap-frame failure; a new capture is required for this revision.

The new fix is in `map-registration.mjs`, an external probe helper imported by
a banner in the new bundle. It uses native Vite 8.3.5's public ModuleRunner
`retrieveSourceMap` option with an empty, non-fetching graph. It does not modify
Vitest, redirect APIs/evaluators/mocks, replace Error.stack, or enable Node's
process-wide source-map mode. Vite performs frame mapping; the adapter only
normalizes validated map source locations for that pinned consumer.
The banner registers before worker bootstrap execution, avoiding a cached null
map on the first thrown frame. All literal lazy framework imports remain native
and lazy, with resolution relative to their original importer.

The registration metadata binds the bundle URL, map URL/hash, and all six
original source URLs/content hashes. A missing map rejects on registration.
Only exact bundle URL/path lookups may load it; JSON parsing, hashing and source
validation happen once at first retrieval. Corrupt/unsupported maps throw rather
than returning fabricated frames. The emitted map is resolved from its actual
file URL; original source paths resolve against that map URL. The known
Rolldown virtual runtime is retained as source plus hash.

Closing the registration owner removes its retrieval hook without clearing
other Vitest runners. Vite's private map/file-content caches are not publicly
removable and can remain until OS process exit. Repeat registration of a bundle
URL rejects. This is a fresh-worker experiment, never a reusable runtime.
Normal SIGTERM/deadline SIGKILL process destruction reclaims all state; graceful
exit also requests hook cleanup. Tests explicitly exercise scoped cleanup.

Run ONE complete capture in a new directory, on Linux with exact Node 26.3.1:

```sh
python3 benchmarks/experiments/vitest5-private-bootstrap/capture.py /tmp/NEW-w13-capture --node /path/to/node --install
```

An existing official distribution archive can be supplied with `--archive` to
avoid downloading it again; it is still checked against fresh upstream HTTPS
SHASUMS256. `--install` is used in native CI and can be omitted locally for the
already pinned installation. The capture verifies both the archive hash and the
extracted `bin/node` hash against the actually executed binary, snapshots source
before prerequisites, and preserves every stdout/stderr/status including failures.
No retries are automatic. Prerequisites/tests/probe and CI each have bounds.

The complete matrix runs inactive/A/B/A with actual arithmetic differences,
exact failures/inventory, distinct PIDs/realm markers and drained fresh forks.
It includes global/env/builtin/local/native-external-module/clock state,
setup/hooks/mocks/spies, late real timers, project TypeScript and bootstrap
original-file/line/column stacks, test/import throws, and synchronous hangs with
a real descendant. Both deadline groups must become entirely empty, including
zombies. Arbitrarily daemonized descendants that escape their group remain
outside this controlled cleanup proof. Coverage/providers/custom loaders are
not universally qualified by this Node-only fixture.

Focused tests cover original importer/conditional export resolution, unchanged
allowlist/native intersection, unsupported syntax, selector rejection controls,
registration cleanup, actual Vite mapping, lazy lookup, exact lookup identity,
missing/corrupt maps and failed-validation caching. The legacy separate
`framework-stack.mjs` preserves the earlier capture procedure; current
qualification uses the unified `probe.mjs` matrix instead.

Installation manifests are gzip-compressed with both compressed and original
SHA-256 retained. Bundle/map/registration/helper/source/fixture/Node hashes and
raw outcomes, job identities, cleanup and map events are retained. The artifact
remains tied to its original installation URLs, not relocatable product output.
Map events record registration/first-lookup CPU and heap deltas and bytes read.
These diagnostics exclude Vite decoding/cache/logging costs and are not whole
tree CPU/RSS, performance ratios or scores. Hashing/JSON/source validation and
one additional registration-owner graph introduce real CPU/I/O/memory costs.
No performance saving is assumed. No product defaults or dependencies change.

Native Ubuntu structural qualification is a separate gate after local success;
product/colord equivalence and an unprofiled paired performance campaign follow
only after that. Main remains unsettled.
