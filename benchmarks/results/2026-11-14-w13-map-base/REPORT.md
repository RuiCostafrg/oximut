# W13 map-base adapter: local structural qualification

Continuation of `e9360b8353344381beaf541465e21d5833d08aca` by the parent GPT Sol.
Earlier commits, failures and evidence remain unchanged. This is an experiment;
product defaults, dependency versions, lockfiles and main are unchanged.

## Change

Vite 8.3.5 resolves map sources with `posixResolve`, then restores the map base's
protocol using `supportRelativeURL`. A relative source under a file-URL base
previously introduced `file:` into a filesystem path. Returning only a path as
the base instead loses the original file-URL representation.

The adapter validates original map bytes and source URLs/hashes as before, then
projects the sources into absolute, percent-encoded URL pathnames. These reset
the decoder's path resolution while preserving URI escapes. The map base stays
a file URL, so Vite restores the protocol itself. This does not rewrite stack
text, change mappings/positions, modify native API owners or enable a process-wide
Node source-map switch. The six-module inline set and 15-module native shared
intersection are unchanged.

## Evidence

Eleven focused tests pass, including real Vite mapping, exact source URI,
absolute file-URL sources, spaces/percent/hash/Unicode filenames, lazy lookup,
scoped cleanup and corrupt/missing map rejection.

The new complete capture passed on Linux arm64 Node 26.3.1 under Android/PRoot.
`local/capture.json` and `local/evidence.json` record the complete qualification
status. The same candidate/helper revision runs the full matrix: inactive/A/B/A
outcomes and inventory, fresh PID/realm identities, state/mocks/hooks checks,
project and exact framework source-map stacks, import/test throws, late timers,
hard deadlines and observed descendant cleanup.

`local/raw-capture.tar.gz` retains the executed source snapshot, full raw capture,
stdout/stderr/statuses, bundle/map/helper hashes, events/fixtures and compressed
installation manifest. Dependency-directory symlinks are excluded; their installed
bytes remain bound by the manifest. `SHA256SUMS` binds the retained files. Runtime
provenance verifies upstream archive, extracted executable and executed binary;
archive/executable hashes are separate. No old artifact was overwritten.

The bundle and map bytes match the prior capture because the helper is imported
externally. The helper hash and source snapshot, not bundle hash alone, identify
this corrected candidate.

## Next gate and limits

`.github/workflows/w13-private-bootstrap.yml` provides bounded native Ubuntu
qualification using the same capture command and pinned dependencies. It runs
only for relevant source pushes on this experiment branch or manual dispatch.
This local pass does not establish native-host qualification, product/colord
equivalence or resource savings. The registration graph, map validation and Vite
caches add unscored CPU/I/O/memory. Coverage, custom loaders and arbitrarily
escaping descendants remain outside the declared structural matrix. No scored
campaign is justified before native qualification passes.
