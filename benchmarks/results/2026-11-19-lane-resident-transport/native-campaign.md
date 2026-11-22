# Native lane-resident transport campaign

The comparison used PR #69 merge `62c08df8fc83c16e9430f48a230e5895776c40ec` as
the base and `c712736a7e317896c27f53428cca7c54f3b82a57` as the measured head.
The measured head is a direct child of the reviewed candidate
`2f94feb3b1c08560fadebf31d740759958c66273`. Git tree comparison shows the
workflow baseline pin and its comment are the only differences. The product,
adapter, lockfiles, corpus and measurement and gate code are byte-identical to
the reviewed candidate. The [pin diff](campaigns/workflow-pin.diff) and the
[raw Git commit object contents](campaigns/workflow-pin.commit) preserve this
provenance after the campaign refs are removed.

Each workflow used a distinct Ubuntu 24.04 x86_64 runner allocation and built both CLI
binaries before timing with `--locked --release -p oximut-cli` in distinct
target directories. The same versions of Node 26.3.1, Vitest 5.1.0 and Stryker
10.1.1 ran with each variant. The adapter tree for each variant was hashed
against its matching Git revision, and the observed executable SHA-256
matched the build record throughout the run. The workflow artifacts contain
binary digests and build metadata.

Each configuration ran one warmup pair and five measured pairs, alternating
before/after and tool order. Each half-pair ran ordinary Vitest, Oximut and
Stryker serially with separate private project copies. Oximut used persistent
controllers, fresh test forks, whole-file selection, order seed 100 and
Oximut caching disabled. Worker limits were matched with Stryker. Each tool
had all 41 source files and all 102 upstream tests available. The small
workload declared five mutation-source files with the arithmetic and Boolean
literal families. The scale workload declared twenty with arithmetic only.

## Verification

Archive recovery used the attempt-1 log ZIP and original artifact ZIP from the
GitHub API. Every member was read to verify CRCs and record its SHA-256.
For each artifact, the retained ZIP size and SHA-256 match the API record.
The per-run `SHA256SUMS` and compressed inventories cover the archived
evidence, including the combined and per-step job logs. The independently
saved API responses, build metadata, summary and verification record are
linked below and duplicated in the preserved material where applicable.

Local verification replayed the existing gate and all deterministic paired
bootstrap calculations from the retained rows. It independently revalidated
the raw test and mutation reports against the vendored corpus, using scratch
copies to map private runner project paths. It checked source hashes, declared
families, exact edited-source identities and outcomes, the killed sentinel,
matching tool scopes, and all 36 tool rows per qualified configuration. The
checks also covered matching tool order, budgets, phase evidence, zero
admission pauses, sample validity, positive finite measurements, and absence
of failed runs, timeouts or descendant cleanup in qualified configurations.

Wall time is monotonic elapsed time for the measured tool command. CPU is
the sum of wait4 user and system rusage, including reaped descendants. RSS is
the peak sampled sum of live process-tree `smaps_rollup` RSS, with a nominal
20 ms sampling interval. The largest observed gaps are recorded per run.
Sampling may miss short peaks and summed RSS counts shared pages in each
process. Builds, installs, report validation and campaign orchestration were
outside the timed commands. The evidence comes from the unprofiled CI runs.

## Recovered runs

All four runs passed the build-integrity and correctness gates and the
independent verification. The exact outcome inventory matched across both
candidate revisions, Stryker, all six rounds, and both worker counts for
each workload. Small colord killed all 10 mutants. Colord-scale killed 202
and had 4 survivors among 206 mutants. All preserved Oximut reports bind the
declared sources to the original hashes. Native coordinator diagnostics show
7, 5, 105, and 54 jobs per subcampaign in the four configurations below,
respectively, with no shared-controller fallback.

Each original results ZIP contains 2,129 files and each run log ZIP contains
25. The compare jobs used distinct runner IDs and the CPU models varied, so
comparisons are within each runner allocation.

| Workload | Workers | GitHub run | Local evidence | Largest sample gap |
| --- | ---: | --- | --- | ---: |
| colord | 2 | [44567404950](https://github.com/RuiCostafrg/oximut/actions/runs/44567404950) | [ZIP](campaigns/colord-2w/artifact-14553890418.zip), [logs](campaigns/colord-2w/logs.zip), [checksums](campaigns/colord-2w/SHA256SUMS), [verification](campaigns/colord-2w/verification.json) | 47 ms |
| colord | 4 | [44567404875](https://github.com/RuiCostafrg/oximut/actions/runs/44567404875) | [ZIP](campaigns/colord-4w/artifact-14551929545.zip), [logs](campaigns/colord-4w/logs.zip), [checksums](campaigns/colord-4w/SHA256SUMS), [verification](campaigns/colord-4w/verification.json) | 145 ms |
| colord-scale | 2 | [44567404778](https://github.com/RuiCostafrg/oximut/actions/runs/44567404778) | [ZIP](campaigns/scale-2w/artifact-14554396303.zip), [logs](campaigns/scale-2w/logs.zip), [checksums](campaigns/scale-2w/SHA256SUMS), [verification](campaigns/scale-2w/verification.json) | 77 ms |
| colord-scale | 4 | [44567404765](https://github.com/RuiCostafrg/oximut/actions/runs/44567404765) | [ZIP](campaigns/scale-4w/artifact-14554775989.zip), [logs](campaigns/scale-4w/logs.zip), [checksums](campaigns/scale-4w/SHA256SUMS), [verification](campaigns/scale-4w/verification.json) | 169 ms |

## Candidate compared with base

Values are medians of the five paired ratios, with 95% percentile bootstrap
intervals in brackets. Each interval uses 10,000 deterministic draws. A ratio
below 1 favors the numerator. All raw pair values are in each run's
`summary.json`.

| Configuration | Wall | CPU | Peak aggregate RSS |
| --- | ---: | ---: | ---: |
| colord 2w | 1.0052 [0.9723, 1.0136] | 1.0027 [0.9814, 1.0215] | 0.9989 [0.9865, 1.0239] |
| colord 4w | 0.9791 [0.9283, 0.9981] | 0.9951 [0.9903, 1.0117] | 0.9992 [0.9570, 1.1162] |
| colord-scale 2w | 0.9484 [0.9337, 0.9657] | 0.9673 [0.9489, 0.9891] | 0.8429 [0.8024, 0.8582] |
| colord-scale 4w | 0.9258 [0.9185, 0.9309] | 0.9675 [0.9661, 0.9749] | 0.8343 [0.8084, 0.8696] |

The scale workload shows an incremental wall and CPU gain at both worker
counts. Median wall time fell 5.2% and 7.4%, CPU fell 3.3% at each worker
count, and sampled peak RSS fell 15.7% and 16.6%. All six intervals favor the
candidate. Small colord at four workers has a 2.1% median wall gain with an
upper bound of 0.9981. Its CPU and RSS intervals include parity, and an RSS
increase of up to 11.6% is consistent with that interval. All three intervals
for small colord at two workers include parity. Every interval either favors
the candidate or spans parity. Five pairs and this corpus leave uncertainty
about smaller regressions and are insufficient to establish statistical
noninferiority. The paired campaign quantifies whole-command effects, and
attribution to individual serialization, parsing or allocation changes
remains unmeasured.

## Comparisons with Stryker

Each Oximut row uses the Stryker run from the same variant and campaign round.
A project goal is supported when the interval upper bound is at most 0.50 for
wall or 0.80 for CPU and RSS. The lower bound exceeds the target for every
candidate metric marked missed below.

### Candidate / Stryker

| Configuration | Wall | CPU | Peak aggregate RSS | Goals met |
| --- | ---: | ---: | ---: | --- |
| colord 2w | 0.7151 [0.7075, 0.7307] | 0.8166 [0.8097, 0.8386] | 0.6301 [0.6258, 0.6341] | RSS |
| colord 4w | 0.6286 [0.6195, 0.6300] | 0.7072 [0.6922, 0.7100] | 0.5947 [0.5809, 0.6209] | CPU, RSS |
| colord-scale 2w | 1.1721 [1.1466, 1.1984] | 1.0024 [0.9755, 1.0081] | 0.7495 [0.7436, 0.7703] | RSS |
| colord-scale 4w | 1.0773 [1.0623, 1.0922] | 0.9543 [0.9450, 0.9630] | 0.6529 [0.6466, 0.6680] | RSS |

The candidate met the RSS goal in all four configurations and the CPU goal
only on small colord at four workers. It missed the wall goal in all four,
and both scale wall intervals remain above Stryker parity.

### Base / Stryker

| Configuration | Wall | CPU | Peak aggregate RSS |
| --- | ---: | ---: | ---: |
| colord 2w | 0.7157 [0.7093, 0.7393] | 0.8188 [0.8100, 0.8285] | 0.6308 [0.6175, 0.6391] |
| colord 4w | 0.6301 [0.6139, 0.6712] | 0.7007 [0.6949, 0.7047] | 0.5924 [0.5499, 0.6164] |
| colord-scale 2w | 1.2408 [1.2113, 1.2463] | 1.0383 [1.0167, 1.0428] | 0.8909 [0.8854, 0.9398] |
| colord-scale 4w | 1.1626 [1.1447, 1.1682] | 0.9822 [0.9763, 0.9892] | 0.7771 [0.7576, 0.8384] |

## Recommendation

The complete matrix qualifies the reviewed candidate as an incremental
improvement on the measured workloads. The parent can consider promotion on
the scale wall and CPU evidence, with the small four-worker RSS uncertainty
recorded. Keep the broader Stryker performance goal open.
