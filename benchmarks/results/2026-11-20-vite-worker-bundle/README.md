# Vitest 5.1 worker bundle diagnostic

This experiment bundles the worker and the public test API into shared ESM
chunks with separate entry facades. A fork-only resolver hook redirects the
stock Vitest URLs to those facades. The normal controller and installed
package files stay intact. That gives test imports and the worker one
collector and mock runtime, while each mutant still gets a fresh process.

Two native Ubuntu x86-64 runs compared one stock capture and one bundled
capture each, at four workers. Every capture passed the historical workload
gate with all 206 exact mutation identities and outcomes, 102 test
identities, whole-file selection and cleanup. The trace confirmed entry
redirection for 214 forks in each candidate, including the eight baseline
forks. All measurements below include diagnostic overhead.

| Generated framework maps | Stock wall / CPU | Bundled wall / CPU | Stock → bundled mutant startup CPU | Stock → bundled controller CPU |
| --- | ---: | ---: | ---: | ---: |
| Runtime visible | 14.86 s / 40.41 s | 23.36 s / 48.09 s | 13.89 → 11.63 s | 4.60 → 14.27 s |
| Hidden, artifacts stored under Vitest dist | 15.91 s / 45.53 s | 15.73 s / 43.70 s | 16.13 → 13.58 s | 5.10 → 6.29 s |

Bundling saved around 11–12 ms of startup CPU per mutant in both captures.
The initial bundle exposed large generated framework maps. Vitest's
controller reads external source and map files during stack processing;
the largest generated map was about 956 KiB. The controller CPU increase
suggests repeated error stack mapping contributed to that regression.
The follow-up kept framework maps as offline artifacts, omitted their
runtime reference, and put the generated files under Vitest's standard
stack-filter prefix. The settings changed together, and the experiment
did not measure map decoding or file-read latency separately. Project and
mutant maps stayed under their stock path.

The second capture recovered the large controller regression, but the
command wall difference was **0.18 s** and sampled peak RSS rose from
**775 MB to 914 MB**. Keep this as a diagnostic and a shared-runtime
identity reference for a possible startup-snapshot experiment. These
captures provide no new unprofiled score.

- [Runtime-visible maps run](https://github.com/RuiCostafrg/oximut/actions/runs/44737168938) and [verified data](mapped.json).
- [Hidden maps run](https://github.com/RuiCostafrg/oximut/actions/runs/44738285846) and [verified data](hidden.json).
- Raw archives, source inventories, build metadata, logs and unpacked
  evidence are saved at `/root/oximut-evidence/vitest-fork-bundle/{native,hidden}`.

```sh
gh workflow run native-diagnostic-profile.yml --ref t3/fresh-worker-startup \
  -f capture_mode=worker-bundle -f workload=colord-scale -f concurrency=4
python3 benchmarks/results/2026-11-20-vite-worker-bundle/summarize.py \
  /root/oximut-evidence/vitest-fork-bundle/hidden \
  /root/oximut-evidence/scale-bottlenecks/workers/artifact/capture
```
