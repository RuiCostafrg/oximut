# Native matched benchmark, 2026-10-27

[Run 40595255153](https://github.com/RuiCostafrg/oximut/actions/runs/40595255153)
validated PR head `10ef25546b89e88e493bf198fd837111461ce8d1` using GitHub's
checked-out merge commit `147cfa5600216b71185096a4fd62424de87c0203`.
All 150 downloaded benchmark artifact files are preserved: reports and metadata
are in `raw/`; the 48 unchanged output logs are in `raw-output-logs.tar.gz`
(with their original `raw/.../output.log` paths). `RAW-ARTIFACT-SHA256SUMS`
verifies the original artifact after extracting the logs. This keeps the review
file count below the service limit while retaining every raw byte.
The artifact ID is 12777800206; GitHub records ZIP digest
`sha256:b2822b282945d268cb4ea7b035836b70b3a376121de9e92fc010d6a15b8f01f7`.

Native Ubuntu 24.04 x86_64; one warmup and five measured pairs at each synthetic
scale. The campaign-wide correctness/measurement gate passed. Exact active edits,
outcomes, baseline/test scope and accurate memory accounting qualified. This
precedes the runtime optimization and real-project experiment; it is not colord.

Median paired Oximut/Stryker wall, CPU, aggregate RSS ratios (lower is better):
small 1.056 / 1.542 / 0.763; scale 2.724 / 3.943 / 0.870.
The 20% faster and 50% lower memory targets were missed at both scales. CPU use
also increased. Complete samples, paired bootstrap intervals, tool versions,
source and binary hashes, reports and failures remain in the raw artifact.
Green CI certifies valid evidence, not successful performance targets.

The earlier invalid native campaign remains separately archived and is not
superseded by rewriting its rows.

`source-artifact-evidence.json` is the unmodified validator evidence from
[run 40595255026](https://github.com/RuiCostafrg/oximut/actions/runs/40595255026),
artifact 12776510286 (ZIP digest
`sha256:6449e15070805e9c7f16c0d9bffff5db8290a25d0a4c36cf79262239344ed3a8`).
It records native installation, source removal, cache/report checks and relocation
of that candidate. The 50MB installed binary/dependency artifact is retained by
GitHub, not vendored here. This is not a final v1 release or its provenance.
