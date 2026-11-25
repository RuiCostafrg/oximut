# Outcome parity corpus

Proves that the fast execution modes report the same per-mutant outcomes as the
reference mode on real open-source Vitest projects, not only the vendored colord
corpus. Run by `.github/workflows/outcome-parity.yml` (manual dispatch, and on pull
requests touching the engine, adapter or this directory).

`corpus.json` pins each project to an exact commit. `configs/<name>.vitest.config.ts`
is copied into the checkout as the Vitest config Oximut runs: the upstream test files
named in `include`, under the isolation Oximut requires. Projects are checked out
under `.parity-work/` inside the repository so they resolve the repository's pinned
Vitest; none of them is installed.

| mode | arguments |
| --- | --- |
| `reference` | `--execution fresh --no-zygote --no-test-selection` |
| `persistent` | `--execution persistent --no-zygote --no-test-selection` |
| `zygote` | `--execution persistent --zygote --no-test-selection --no-zygote-precollect --no-rolling-lanes` |
| `defaults` | none: the defaults (persistent, test selection, zygote, precollect, rolling lanes) |

Every mode runs with `--workers 4 --no-cache`. Mutants are keyed by the exact edited
file (`benchmarks/m6.py` `mutation_identity`), and `mutation_identity_outcome_sha256`
is the digest of `{identity: outcome}`, as in the colord benchmark reports.

Any difference fails the run: one line per differing mutant (id, `file:line`, mutator,
reference outcome, mode outcome), and the digests of every mode in the job summary. A
mode also fails when its diagnostics show it did not run as named (zygote off, no
forks, a requested flag not reported on) or when the run is not complete.
A `survived` mutant that test selection reports as `no-coverage` counts as a
difference; it is not normalised away.

```sh
cargo build --release -p oximut-cli
python3 scripts/build-zygote-addon.py
python3 benchmarks/parity/run_parity.py --projects ufo pathe
python3 benchmarks/tests/test_parity.py
```
