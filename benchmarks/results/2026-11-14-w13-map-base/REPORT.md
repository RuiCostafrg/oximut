# W13 map-base adapter: local and native structural qualification

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
The native run below now establishes the declared fixture's native-host structural
qualification. Neither pass establishes product/colord equivalence or resource
savings. The registration graph, map validation and Vite
caches add unscored CPU/I/O/memory. Coverage, custom loaders and arbitrarily
escaping descendants remain outside the declared structural matrix. No scored
campaign or product integration was performed during this evidence recovery.

## Native structural evidence recovery

[Run 43644805918](https://github.com/RuiCostafrg/oximut/actions/runs/43644805918)
completed successfully on the first attempt at exact head
`e966f1f0c885e5ad8a9a988170cc571f971e2545`. Its single `structural` job passed
on Ubuntu 24.04.5 x64, image `ubuntu-24.04` / `20261111.127.1`. Recovery made no
product, probe, workflow or dependency changes and reran no experiment. The
parent's adapter commit and all previous artifacts remain intact. Main remains
unsettled; W11/PR67/main were not acted on.

The recovery independently checked the raw artifact, not only GitHub's green
conclusion or the local report:

- Capture status is `complete structural qualification passed on this host;
  no performance qualification`. Install, focused and matrix subprocesses all
  exit zero. Focused output records **11 tests, 11 passes, zero failures**, including
  real Vite full URI mapping and absolute/escaped space, percent, hash and Unicode
  filenames. Captured source confirms those tests were the intended tests.
- `commit.txt`, workflow run metadata and artifact metadata all name exact head
  `e966f1f`. **Every one of 14 regular files in `source.tar.gz` matches that Git
  tree byte-for-byte**, covering the entire probe directory, package/lock files,
  Node version file and executed workflow. The probe's own source-manifest hashes
  match those same bytes. No untracked source or another revision was substituted.
- The installed-file manifest's compressed and uncompressed hashes verify.
  Its module hashes match the audited topology, all six inline modules remain
  selected, all 15 public/API intersection modules remain native, and no audited
  native literal edge enters the inline set. Package-lock and recorded tool
  versions agree. Bundle, map, registration metadata, helper and fixture hashes
  verify against the raw artifact and exact source snapshot.
- Stock/candidate inactive/A/B/A runs have identical inventory and matching
  selector-specific results: inactive passes; A fails add only; B fails multiply
  only; A again fails add only. Expected error messages and completion reasons
  reconcile. Setup selectors and all admission identities match the jobs.
- All **16 worker PIDs and 16 realm UUIDs are distinct**. Fourteen normal workers
  have recorded birth identities and permitted exit statuses. Both hard-deadline
  workers and their actual descendants are observed before SIGKILL. All **10
  controller process groups are completely empty** afterward, including zombies.
- Both bootstrap stacks contain exactly
  `file:///home/runner/work/oximut/oximut/node_modules/vitest/dist/chunks/base.Cof-UHJf.js:123:9`.
  There is no extra `file:` component or generated bundle location. All eight
  project map observations preserve `mapped.ts:2:9`. State/clock contamination,
  external CJS/ESM freshness, mocks/setup/hook counts, test/import throws and the
  absence of late timer events also reconcile with the raw results/events.

The raw probe summary retains its generic text `complete local structural matrix
passed; native qualification pending`. It is not edited: this invocation ran on
the verified native runner, and the workflow, runtime, complete capture status and
independent recovery establish the native pass. `native/verification.json` records
that qualification conclusion explicitly.

### Exact runtime and artifact provenance

Runtime is Node **26.3.1**, npm **11.16.0**, Linux x64, using
`/opt/hostedtoolcache/node/26.3.1/x64/bin/node`. The capture verifies fresh upstream
HTTPS checksums, distribution archive bytes, the archive's extracted `bin/node`
bytes, and equality to the actual executed executable. The executable hash is
`0b52710a27133a3baf45f0d1f10689ab874f50e5821d8e611486d17e20bcacdd`;
the official `node-v26.3.1-linux-x64.tar.xz` archive hash is
`55647180e4ae58ffeaa3294e89aa4abda7c371dfbd64b44cbdb022980177aae0`.
These x64 hashes are distinct from the separately verified local arm64 hashes.
Archive and executable hashes are not conflated. This is HTTPS checksum and
byte equality verification, not a signature-verification claim.

Recorded dependencies remain Vitest/coverage-v8 **5.1.0**, Vite **8.3.5**,
Rolldown **1.2.15**, Babel parser **8.0.10**, and magic-string **1.4.5**.
The native artifact hashes are:

- Worker: `0f22102834b1bc2ad5ffc284cde71374db50fce0f649d0d05bdbbfad95240fad`.
- Map: `4478f9c9e627e200a7fa75ebe9d931152c93301248a95c5e6349155db159109e`.
- Registration metadata: `a87722a8278fa5a95f0cb01ee4ccd9cba11ede6f05328fb3871000d65ddb877e`.
- External helper: `f080d1aaddbfd117a3374986d5bdaa487dcd95e13d4bf6b1baa2194ca6f0fa83`.

Paths in the native artifact differ from local installation paths, so native
bundle/map bytes need not match local bytes. The external helper hash is verified
against tested head, not inferred from bundle identity alone.

### Retained files and scope

`native/artifact.zip` is the **complete 93-file raw GitHub artifact**, ID
`14133765404`, name
`w13-private-bootstrap-e966f1f0c885e5ad8a9a988170cc571f971e2545-1`.
Its SHA-256
`c19aabf5b98a51486a9471fe6ff831aa58b597320ee857dc4e1effd6edd4f396`
matches GitHub's API digest. `native/logs.zip` retains all **13 raw workflow log
files**, including runner/setup/prerequisite/upload logs, with ZIP integrity and
per-file hashes verified. This persists all raw evidence beyond GitHub's 14-day
artifact expiry. Four pre-existing local stdout/stderr files that were ignored by Git, despite
being listed in the local checksum manifest, are now also tracked byte unchanged.
They were checked against the existing committed raw local archive.
No giant plaintext dependency manifest was added: its original
compressed copy and both hashes remain in the artifact.

`native/archive-file-hashes.json` hashes every uncompressed artifact/log entry.
`native/verification.json` records independent recovery results and all source
snapshot hashes. `native/verification-source.py` is the exact offline recovery
checker used against the downloaded evidence and tested Git tree; it is not a
new probe or candidate. `native/run.json`, `jobs.json` and `artifacts.json` retain
GitHub metadata. Readable capture/runtime/focused/evidence summaries are also
retained. `native/SHA256SUMS` and the root `SHA256SUMS` bind retained recovery files.

This passes **only the declared native structural fixture**. It does not establish
coverage/custom-loader support, arbitrary escaping-descendant cleanup, general
Vitest compatibility, product/colord equivalent-mutant correctness, relocation,
or any speedup. Existing callback CPU/I/O/heap/cache diagnostics are preserved,
not converted into scored tree CPU, aggregate peak RSS, ratios or confidence
intervals. There were no profilers, scored runs, PRs, merges or campaign retries.
The next justified gate is a separately authorized bounded opt-in integration /
product-colord correctness qualification with the same singleton/isolation and
deadline/resource gates, followed only later by an unprofiled paired performance
campaign. Product defaults remain fresh-process; main remains unsettled.
