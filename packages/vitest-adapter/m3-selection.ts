import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { TestModule, Vitest } from 'vitest/node';
import type { Coverage, Request, Result } from './protocol.ts';

const setup = fileURLToPath(new URL('./m3-setup.ts', import.meta.url));

/** Probe streams are compact (a few bytes per hit); beyond this coverage is incomplete. */
const MAX_PROBE_BYTES = 512 * 1024 * 1024;
/** Hit bitsets (mutants hit times tests / 8) held while the streams are read. */
const MAX_BIT_BYTES = 1024 * 1024 * 1024;
/** Up to this many (mutant, test) hits the coverage lists explicit test ids. */
const MAX_EXPLICIT_PAIRS = 20000;

function popcount(word: number): number {
  let value = word - ((word >>> 1) & 0x55555555);
  value = (value & 0x33333333) + ((value >>> 2) & 0x33333333);
  return Math.imul((value + (value >>> 4)) & 0x0f0f0f0f, 0x01010101) >>> 24;
}

/** The set bits of `row` below `count` as inclusive `[first, last]` runs, ascending. */
function runs(row: Uint32Array, count: number): [number, number][] {
  const found: [number, number][] = [];
  let first = -1;
  for (let word = 0; word < row.length; word++) {
    const bits = row[word];
    // Whole words that are empty or full never need a bit-by-bit look.
    if (bits === 0 || bits === 0xffffffff) {
      if (bits === 0 && first >= 0) {
        found.push([first, word * 32 - 1]);
        first = -1;
      } else if (bits !== 0 && first < 0) first = word * 32;
      continue;
    }
    for (let bit = 0; bit < 32; bit++) {
      const at = word * 32 + bit;
      if (at >= count) break;
      if (bits & (1 << bit)) {
        if (first < 0) first = at;
      } else if (first >= 0) {
        found.push([first, at - 1]);
        first = -1;
      }
    }
  }
  if (first >= 0) found.push([first, count - 1]);
  return found;
}

/** The set bits of `row`, ascending. */
function positions(row: Uint32Array, count: number): number[] {
  return runs(row, count).flatMap(([first, last]) =>
    Array.from({ length: last - first + 1 }, (_, offset) => first + offset),
  );
}

export class SelectionSupport {
  readonly observed = new Set<string>();
  private directory?: string;

  begin(vitest: Vitest, request: Request): void {
    this.close();
    this.observed.clear();
    for (const project of vitest.projects) {
      project.config.setupFiles = project.config.setupFiles.filter((file) => file !== setup);
    }
    if (!request.collectCoverage) return;
    this.directory = mkdtempSync(join(tmpdir(), 'oximut-m3-'));
    process.env.OXIMUT_M3_COVERAGE = this.directory;
    for (const project of vitest.projects) {
      project.config.setupFiles.unshift(setup);
    }
  }

  finish(
    vitest: Vitest,
    request: Request,
    modules: ReadonlyArray<TestModule>,
    result: Result,
  ): Coverage | undefined {
    if (!request.collectCoverage) return undefined;
    const reasons = new Set<string>();
    if (request.collectCoverage.some((probe) => !this.observed.has(resolve(probe.file))))
      reasons.add('source-not-transformed');
    const tests: Coverage['tests'] = [];
    if (vitest.projects.length !== 1) reasons.add('multiple-projects');
    for (const project of vitest.projects) {
      const config = project.config;
      // globalSetup runs in the host process, outside every probe and test window, and
      // may seed state shared across files, so it makes attribution unsound run-wide.
      // Per-file setupFiles do not: their hits are attributed (see m3-setup.ts).
      if (config.globalSetup?.length) reasons.add('global-setup');
      if (config.environment !== 'node') reasons.add('unsupported-environment');
      if (config.server?.deps?.external?.length) reasons.add('externalized-dependencies');
      if (config.runner) reasons.add('custom-runner');
      if (config.sequence.shuffle) reasons.add('shuffled-tests');
      if (
        project.vite.config.plugins.some(
          (plugin) =>
            !/^(?:vite:|vitest(?::|$)|oximut-m[12]-mutation$|builtin:|alias$)/.test(plugin.name),
        )
      )
        reasons.add('custom-plugins');
    }
    for (const module of modules) {
      for (const test of module.children.allTests()) {
        tests.push({ id: test.id, file: resolve(module.moduleId), name: test.fullName });
        if (test.options.concurrent) reasons.add('concurrent-tests');
        if (test.options.retry || test.options.repeats) reasons.add('retried-or-repeated-tests');
        if (test.result().state !== 'passed') reasons.add('unexecuted-or-failed-tests');
      }
    }
    // The inventory is sorted first: hits are kept as bits over positions in it, and
    // leave as index runs, so memory and payload follow the pairs hit, not suite size.
    tests.sort((a, b) => a.id.localeCompare(b.id));
    const position = new Map(tests.map((test, index) => [test.id, index]));
    const words = (tests.length + 31) >>> 5;
    // Explicit empty entries distinguish unexecuted candidates from truncation.
    const slotOf = new Map<string, number>();
    for (const mutation of request.collectCoverage) {
      for (const match of mutation.code.matchAll(/__oximut_m3_hit__\?\.\("([a-f0-9]{64})"\)/g))
        if (!slotOf.has(match[1])) slotOf.set(match[1], slotOf.size);
    }
    const bits: (Uint32Array | undefined)[] = new Array(slotOf.size);
    const outside = new Uint8Array(slotOf.size);
    let bitBytes = 0;
    let ready = 0;
    try {
      const streams = readdirSync(this.directory as string)
        .sort()
        .map((file) => join(this.directory as string, file));
      // Check the aggregate size before loading any stream into memory.
      const size = streams.reduce((total, file) => total + statSync(file).size, 0);
      if (size > MAX_PROBE_BYTES) throw new Error('probe-record-limit');
      for (const file of streams) {
        const data = readFileSync(file);
        // Mutant slots of this stream, in the order the process first saw them.
        const table: number[] = [];
        for (let start = 0; start < data.length; ) {
          let end = data.indexOf(10, start);
          if (end < 0) end = data.length;
          const line = data.toString('utf8', start, end);
          start = end + 1;
          if (!line) continue;
          const record = JSON.parse(line) as {
            ready?: boolean;
            incomplete?: string;
            ids?: string[];
            outside?: number[];
            test?: string | null;
            hits?: number[];
          };
          if (record.ready) {
            ready++;
          } else if (record.incomplete) {
            reasons.add(record.incomplete);
          } else if (record.ids) {
            for (const id of record.ids) {
              const slot = slotOf.get(id);
              if (slot === undefined) reasons.add('unknown-probe');
              table.push(slot ?? -1);
            }
          } else if (record.outside) {
            for (const index of record.outside) {
              const slot = table[index];
              if (slot === undefined) reasons.add('missing-or-invalid-probe-records');
              else if (slot >= 0) outside[slot] = 1;
            }
          } else if (record.hits) {
            const at = typeof record.test === 'string' ? position.get(record.test) : undefined;
            if (at === undefined) reasons.add('unknown-test-identity');
            for (const index of record.hits) {
              const slot = table[index];
              if (slot === undefined) reasons.add('missing-or-invalid-probe-records');
              else if (slot >= 0 && at !== undefined) {
                let row = bits[slot];
                if (!row) {
                  bitBytes += words * 4;
                  if (bitBytes > MAX_BIT_BYTES) throw new Error('probe-record-limit');
                  row = bits[slot] = new Uint32Array(words);
                }
                row[at >>> 5] |= 1 << (at & 31);
              }
            }
          } else {
            throw new Error('unknown probe record');
          }
        }
      }
    } catch (error) {
      reasons.add(
        error instanceof Error && error.message === 'probe-record-limit'
          ? 'probe-record-limit'
          : 'missing-or-invalid-probe-records',
      );
    }
    if (ready !== modules.length) reasons.add('incomplete-setup-installation');
    const complete = result.outcome === 'passed' && reasons.size === 0;
    // Few hits stay explicit ids; many become index runs (the CLI reads either).
    let pairs = 0;
    for (const row of bits) for (const word of row ?? []) pairs += popcount(word);
    const explicit = pairs <= MAX_EXPLICIT_PAIRS;
    return {
      complete,
      tests,
      hits: [...slotOf.keys()].map((mutantId, slot) => {
        const row = bits[slot];
        const hit: Coverage['hits'][number] = {
          mutantId,
          tests: [],
          outsideTest: outside[slot] === 1,
        };
        if (!row) return hit;
        if (explicit)
          hit.tests = positions(row, tests.length)
            .map((at) => tests[at].id)
            .sort();
        else hit.testRanges = runs(row, tests.length);
        return hit;
      }),
      reasons: [...reasons].sort(),
    };
  }

  close(): void {
    delete process.env.OXIMUT_M3_COVERAGE;
    if (this.directory) rmSync(this.directory, { recursive: true, force: true });
    this.directory = undefined;
  }
}
