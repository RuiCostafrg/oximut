# Local CI — `gh-signoff` (Linux only)

Run the Linux feasibility checks on your own machine and post green
`signoff/<stage>` commit statuses with [basecamp/gh-signoff](https://github.com/basecamp/gh-signoff).

Use it when free Actions minutes run out — or as a fast pre-push gate. The GitHub
Actions workflows stay untouched; this lane covers **Linux only**.

## Setup (Linux)

```bash
gh auth login
gh extension install basecamp/gh-signoff
```

Toolchains: Node (`.node-version` pins 26.x), Python 3.13, Rust 1.96.0 (pinned via
`rust-toolchain.toml`; rustup selects it automatically).

## Usage

```bash
scripts/local-ci.sh                # stages: tests lint security
scripts/local-ci.sh lint           # run selected stages only
scripts/local-ci.sh --no-signoff   # checks only, no statuses posted
```

Push your commits first: `gh signoff` only attests pushed work. Failing stages post
a red `signoff/<stage>` status automatically. Each status (green or red) is bound
with `--commit` to the exact commit captured at the start of its stage, so a
commit or push landing mid-run can never be green-lit for code it did not test.
a red `signoff/<stage>` status automatically. The default all-stage invocation
(`scripts/local-ci.sh`) mirrors the `ci.yml` feasibility job (candidate sandbox,
npm, Rust, Python, benchmarks smoke); a single stage covers only that stage —
expect several dozen minutes on first run while caches warm up.

## Optional: make sign-off a merge requirement

```bash
gh signoff install tests lint security
gh signoff uninstall
```

## Notes

- Heavier benchmark/benchmark-matrix workflows (`real-project-benchmark.yml`,
  `matched-benchmark.yml`, `warm-runtime-probe.yml`, …) stay in Actions; they are
  research jobs, not everyday checks.
