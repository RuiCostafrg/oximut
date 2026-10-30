This is an upstream production workload from [omgovich/colord](https://github.com/omgovich/colord/tree/1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff),
commit `1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff` (upstream package version
`2.10.0`). The MIT license and copyright notice are preserved in LICENSE.md.
`upstream-package.json` is the original package metadata, not the benchmark's
installation manifest. Dependencies come from Oximut's exact root lockfile.

All 41 files under src/ and all four upstream *.test.ts suites plus their
fixtures.ts helper are copied byte-for-byte. workload.json records SHA-256 hashes
for all 46 files and the exact 102 test identities. No production source or test
assertions were patched. Upstream tests/benchmark.ts is an unrelated benchmark
program and is not included. The only test harness adaptation is the separate
vitest.config.ts: Vitest globals replace Jest globals, with forks, one worker,
file isolation and no file parallelism. No test is skipped. The original 500ms
parser complexity assertions remain enabled.

The full production graph is available. The mutation scope is explicitly five
files: four manipulation modules and the plugin registration module. Tests cover
public conversions, parsers, plugins, registration state and scientific reference
data. Upstream has no asynchronous test suite; this workload does not claim
async coverage. It does exercise real module state through plugin registration.

The declared comparison families are arithmetic and boolean-literal. There are
no Boolean literals in these five source files. An unrestricted old Oximut is
allowed only with an explicit diagnostic flag; extra mutations invalidate ratios.
The corpus was not rewritten to force operator agreement.

Reproduce provenance independently:

```sh
git clone https://github.com/omgovich/colord.git upstream-colord
git -C upstream-colord checkout 1404cfe80cf8fe6df1a876ea3b12aa8eccc7feff
# Compare src/, tests/{colord,plugins,delta-e00,parse-complexity}.test.ts,
# tests/fixtures.ts, LICENSE.md and upstream package.json byte-for-byte.
```

workload.json also pins `expectedOximutTests`. The ordinary JSON reporter's
fullName separates suite titles with spaces; Vitest's programmatic TestCase
fullName (used by Oximut coverage) separates them with ` > `. The second list is
built from the ordinary report's ancestorTitles and title, joined with ` > `.
Each tool must match its complete exact list, including multiplicities, before
receiving the shared declared-test digest. Emitted names are never fuzzily
rewritten, and missing or duplicated tests fail the gate.

## Known upstream behavior

The pinned snapshot's `src/helpers.ts` `parseHue` looks up angle units without
lowercasing them, while the HSL, HWB and LCH string matchers accept mixed case.
For example, `parseHue("100", "grad")` returns `90`, but
`parseHue("100", "GRAD")` returns `100`; `parseHue("0.25", "turn")` returns
`90`, but `parseHue("0.25", "TURN")` returns `0.25`. Unknown lookup keys fall
back to a conversion factor of one. The upstream tests do not cover these
mixed-case angle-unit examples.

This behavior is retained as part of the byte-identical upstream workload.
`helpers.ts` is available to both tools through the production import graph but
is outside the five declared mutation files. The benchmark compares the tools
on the same upstream tests and edits; it does not certify colord's functional
correctness or complete input coverage. Fixing the upstream implementation or
adding assertions would require a separately identified workload revision and
new qualification; it must not silently change the existing corpus or results.
