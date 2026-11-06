# Native fresh-fork bundle structural oracle

[Run 42150504514](https://github.com/RuiCostafrg/oximut/actions/runs/42150504514),
Ubuntu 24.04 x86_64, Node 26.3.1, Vitest 4.1.12, Rolldown 1.2.15.
Reviewed head `2959d0bf088f8d0e2fdf05d13010c501f0de190f`; tested merge
`c76b31b83cb65f39a27c834e387717808f6a2e80`.

**Structural oracle PASS:** stock and bundled modes each passed 16/16 assertions,
with identical fixture hashes, distinct PIDs and realms, normal worker shutdown,
and empty private process groups. This is one fixed-order structural attempt,
not a scored benchmark. The exact colord 102-test/10-mutant gate was not run.
No performance gain is established.

| Required performance metric | Evidence |
|---|---|
| Whole-command wall | Unmeasured; controller/stage clocks below have narrower scopes |
| Whole-tree CPU, user/system | Unmeasured/N/A |
| Peak aggregate RSS | Unmeasured/N/A |
| Peak/observed aggregate process counts | Unmeasured/N/A; worker identities are retained separately |
| Oximut `resources.phaseWallMs` | N/A; this standalone probe does not run the CLI |
| Paired Oximut/Stryker ratios and bootstrap 95% CIs | N/A |
| Native product correctness/target qualification | N/A; structural fixture only |

The following are raw monotonic diagnostic intervals, in milliseconds. They
include probe overhead and have no paired confidence intervals. Nested scopes
must not be added. The two controllers run consecutively, stock first.

| Scope | Stock | Bundled |
|---|---:|---:|
| Parent launch to controller entry observation | 30.214158 | 28.199989 |
| `createVitest` and standalone initialization | 30.744684 | 31.480716 |
| First specification, through worker drain | 109.546543 | 114.632625 |
| Second specification, through worker drain | 89.818970 | 97.254793 |
| Vitest close | 1.584264 | 1.595691 |
| Parent cleanup/result handling | 0.657566 | 0.558837 |
| Whole controller lifetime observed by parent | 411.429296 | 401.900796 |

Bundle setup/build took 324.879454 ms, including 31.280186 ms loading the
bundler/context tools. It emitted 1,054,053 bytes from 72 static framework modules,
with the native collector API retained separately. Controller `vitest/node`
import, pool-module import, specification discovery, and run-versus-drain timing
were not separately recorded by this revision. Subsequent probe code adds those
clocks; no values are inferred for this historical run.

Native worker PIDs were 2348/2355 (stock) and 2377/2385 (bundled), all with normal
SIGTERM after framework stop. Controllers 2332/2362 exited with code 0. Their
start identities, flags, entrypoints, realms, results and cleanup records are
retained in the JSON files. Identity evidence is not a process-tree peak count.

`evidence-source.json` records provenance and hashes for the **omitted** original
bundle and source archive. The complete workflow artifact has 14-day retention;
digests cannot reconstruct missing files after expiry. The bundle contains
installation-bound absolute URLs and is not a relocatable release artifact.
The probe source is retained at the reviewed head, with the lockfile and module
hashes recorded by the capture. `SHA256SUMS` covers every retained capsule file.
