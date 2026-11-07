# Bottleneck report (2026-11-07, main `2c6952b`)

Open the pages in a browser (on GitHub: Raw → save → open). They are offline HTML, with no scripts or network access, and support light and dark themes:

- [`index.html`](index.html): where Oximut stands against Stryker, where the wall time goes, and the five ranked bottlenecks with improvement candidates
- [`timeline.html`](timeline.html): one run, job by job; every fresh Vitest fork's lifecycle (bootstrap, fetch/collect, test run, exit)
- [`cpu.html`](cpu.html): Node CPU by category and function (forks vs controller), process CPU, the Rust memory sampler's cost, and memory at each tool's peak

## Inputs

| Evidence | Run | Use |
|---|---|---|
| Paired before/after campaign (5 pairs, gates pass) | [42368931548](https://github.com/RuiCostafrg/oximut/actions/runs/42368931548) | Every ratio, CI, phase median and tool median |
| Rust sampler accounting | [42370674832](https://github.com/RuiCostafrg/oximut/actions/runs/42370674832) | Sampler self CPU; peak RSS composition |
| Controller job timeline | [42370841803](https://github.com/RuiCostafrg/oximut/actions/runs/42370841803) | Fork lifecycle and job walls (shares only) |
| Node CPU profiles (explicit `--cpu-prof`) at `2d2911f`: main `2c6952b` merged into the #43 profiler-tooling branch, so the product code matches main but the capture tooling differs | [42370984859](https://github.com/RuiCostafrg/oximut/actions/runs/42370984859) | CPU categories and functions; 17 of 18 fork profiles (partial evidence; that capture's strict gate fails) |

`data.json` holds every derived number plus the SHA-256 of every consumed input file. Extraction rejects a campaign whose gates failed, whose rows are missing or duplicated, or whose published medians/intervals differ from those recomputed from its raw rows (same deterministic bootstrap). It also rejects inputs that declare different workloads, and records each diagnostic's commit and pass status. Regenerate the pages without the (14-day) artifacts:

~~~sh
python3 benchmarks/bottleneck_report.py render --data benchmarks/results/2026-11-07-bottleneck-report/data.json \
  --output-dir benchmarks/results/2026-11-07-bottleneck-report
~~~

Re-extract from downloaded artifacts with `bottleneck_report.py extract --help`.

## Limits

- Only the paired campaign is scored. The three diagnostic captures are single runs carrying profiler or clock overhead, and they may land on faster or slower runner hosts (the controller capture's host took 53% of the campaign's Stryker wall time, running the same Stryker code). Within a capture the report compares shares; it never subtracts absolute times across captures.
- CPU profiles sample the main thread only. Process CPU from getrusage includes all threads.
- Stryker's worker processes are terminated by signal and leave no profile, so the Node-level breakdown covers Oximut only.
- Improvement candidates are discussion items, not measured savings.
