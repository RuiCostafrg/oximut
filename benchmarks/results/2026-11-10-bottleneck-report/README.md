# Bottleneck report: shared lanes at 4 workers (2026-11-10, main `c9c116f`)

Open the pages in a browser (on GitHub: Raw → save → open). They are offline HTML, with no scripts or network access, and support light and dark themes:

- [`index.html`](index.html): where Oximut stands against Stryker at 4 and 2 workers, where the 2.7 s goes, the controller timeline of one run, and six ranked bottlenecks with estimated savings
- [`timeline.html`](timeline.html): one run at 4 workers, fork by fork (spawn, bootstrap, fetch/collect, test run, exit), controller CPU against job wall, and the gaps between controller jobs
- [`cpu.html`](cpu.html): Node CPU by category and function (forks vs the shared controller) and whole-tree process totals

This is the shared-lane successor of the [2026-11-07 report](../2026-11-07-bottleneck-report/README.md), which covers one controller per worker at one worker.

## Inputs

| Evidence | Run | Use |
|---|---|---|
| Paired before/after campaign at 4 workers (5 pairs, gates pass; before = after = main `c9c116f`) | [42808611154](https://github.com/RuiCostafrg/oximut/actions/runs/42808611154) | Ratios, CIs, phase medians, tool medians |
| Same campaign at 2 workers | [42808599890](https://github.com/RuiCostafrg/oximut/actions/runs/42808599890) | Scaling from 2 to 4 workers |
| Controller job timeline at 4 workers (`controller-jobs`, passes) at `805e6a0`: main plus tooling that records shared-lane batches | [42809383591](https://github.com/RuiCostafrg/oximut/actions/runs/42809383591) | Fork lifecycle, job walls, controller CPU per job, gaps |
| Node CPU profiles at 4 workers (explicit `--cpu-prof`) at `1b24aba`: main plus the #43 profiler tooling | [42809377478](https://github.com/RuiCostafrg/oximut/actions/runs/42809377478) | CPU categories and functions; 17 of 18 fork profiles (partial evidence; that capture's strict gate fails) |

`data.json` holds every derived number plus the SHA-256 of every consumed input file. Extraction rejects campaigns whose gates failed, whose rows are missing or duplicated, or whose published medians/intervals differ from those recomputed from the raw rows; inputs that declare different workloads or different concurrency; a controller capture that did not pass or shows no lane batches. Regenerate the pages without the (14-day) artifacts:

~~~sh
python3 benchmarks/bottleneck_report_lanes.py render --data benchmarks/results/2026-11-10-bottleneck-report/data.json \
  --output-dir benchmarks/results/2026-11-10-bottleneck-report
~~~

Re-extract from downloaded artifacts with `bottleneck_report_lanes.py extract --help`; pass `--notes benchmarks/results/2026-11-10-bottleneck-report/notes.json` to record experiment outcomes (the rejected overlap and lane-prepare experiments) next to the ranked bottlenecks they concern. The notes are stored in `data.json`, not in the renderer.

## Limits

- Only the paired campaigns are scored. The two diagnostic captures are single runs carrying clock or profiler overhead and may land on faster or slower runner hosts than the campaigns; durations are compared only inside one capture. The 2-worker and 4-worker campaigns also ran on different hosts.
- The first mutant batch of the controller capture also carries that mode's CPU-profile window.
- CPU profiles sample main threads only; process CPU from getrusage includes all threads. The lane forks of the profiled run left no complete profile.
- Savings are upper bounds computed from one capture's timestamps. They overlap and are not additive; none is a measured result.
- No product code changed. The tooling changes: `controller-jobs.cjs` and `profile_native.py` record shared-lane batches, `--concurrency` for the diagnostic workflow, and the #43 profiler tooling is carried by the CPU capture ref only.
