# Invalid native candidate comparison: build reuse

**This campaign is invalid as a declared before/after candidate comparison. No
performance target or final-candidate improvement is established by its ratios.**

[Run 40629199290](https://github.com/RuiCostafrg/oximut/actions/runs/40629199290)
used PR head `2a121437c91a8f5f42c879eebd5e2f5e67a41dfc`, checked-out GitHub
merge commit `dbe4ff2a3fc9063e19b55ff8f9ee70db1a9dd1a9`, on native Ubuntu
24.04 x86_64. The older workload/measurement gate passed with one warmup and five
measured pairs, all 102 tests and 10 identical killed arithmetic edits. That gate
did not validate the expected difference between the built candidates.

Both copied binaries have SHA-256
`6f2ff9e9606f79026d61d1412f77d8775d6d9585e357f90fbe690e562224d6da`.
All after reports lack the phase fields required by the candidate Rust source.
The shared Cargo target directory reused the baseline executable. The retained
`workflow.log` shows the first build taking 31.46s and the second only 0.05s. Thus the run
combined that executable with the newer adapter, rather than evaluating the
complete declared after candidate. Different adapter hashes cannot establish
correct Rust build provenance. The original generated ratios are not certified.

`artifact.tar.gz` retains every downloaded artifact file, including full private
projects, raw reports/logs, binary hashes, build metadata, samples and the
original generated summary. Its contents preserve the downloaded file bytes;
it is a new container, not a copy of GitHub's ZIP. The original ZIP artifact ID
is 12790991457, with GitHub digest
`sha256:e8b828ae35d4efcfc9e0b68f0bfd5322caf208c7ffa2c049d19d5fd37a78cbf1`.
The original summary and build metadata are also copied alongside for inspection.
`revalidation.json` applies the stricter build/phase gate without altering any
original row: the whole campaign fails and all comparisons are suppressed.

The workflow now uses separate Cargo target directories, validates commit/hash
bindings, expects distinct executables for this pinned Rust change, and requires
complete after phase evidence plus all five per-file disabled checks. Generic
adapter-only experiments can intentionally use identical executables without
that strict policy. Build preflight failures retain evidence and run no timings.
A new native campaign is required; failed evidence is never rewritten into success.
