import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { TestModule, Vitest } from 'vitest/node';
import type { Coverage, Request, Result } from './protocol.ts';

const setup = fileURLToPath(new URL('./m3-setup.ts', import.meta.url));

export class SelectionSupport {
  readonly observed = new Set<string>();
  private directory?: string;
  private recordFile?: string;

  begin(vitest: Vitest, request: Request): void {
    this.close();
    this.observed.clear();
    for (const project of vitest.projects) {
      project.config.setupFiles = project.config.setupFiles.filter((file) => file !== setup);
    }
    if (!request.collectCoverage) return;
    this.directory = mkdtempSync(join(tmpdir(), 'oximut-m3-'));
    this.recordFile = join(this.directory, 'hits.jsonl');
    process.env.OXIMUT_M3_COVERAGE = this.recordFile;
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
      if (config.globalSetup?.length) reasons.add('global-setup');
      if (config.setupFiles.some((file) => file !== setup)) reasons.add('setup-files');
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
    const hits = new Map<string, { tests: Set<string>; outsideTest: boolean }>();
    // Explicit empty entries distinguish unexecuted candidates from truncation.
    for (const mutation of request.collectCoverage) {
      const ids = [...mutation.code.matchAll(/__oximut_m3_hit__\?\.\("([a-f0-9]{64})"\)/g)].map(
        (match) => match[1],
      );
      for (const id of ids) hits.set(id, { tests: new Set(), outsideTest: false });
    }
    let ready = 0;
    try {
      const data = readFileSync(this.recordFile as string);
      if (data.length > 8 * 1024 * 1024) throw new Error('probe-record-limit');
      for (const line of data.toString('utf8').trim().split('\n')) {
        if (!line) continue;
        const record = JSON.parse(line) as {
          ready?: boolean;
          incomplete?: string;
          mutantId?: string;
          testId?: string | null;
        };
        if (record.ready) {
          ready++;
          continue;
        }
        if (record.incomplete) {
          reasons.add(record.incomplete);
          continue;
        }
        const hit = hits.get(record.mutantId as string);
        if (!hit) {
          reasons.add('unknown-probe');
          continue;
        }
        if (record.testId === null) hit.outsideTest = true;
        else if (
          typeof record.testId === 'string' &&
          tests.some((test) => test.id === record.testId)
        )
          hit.tests.add(record.testId);
        else reasons.add('unknown-test-identity');
      }
    } catch {
      reasons.add('missing-or-invalid-probe-records');
    }
    if (ready !== modules.length) reasons.add('incomplete-setup-installation');
    const complete = result.outcome === 'passed' && reasons.size === 0;
    return {
      complete,
      tests,
      hits: [...hits].map(([mutantId, hit]) => ({
        mutantId,
        tests: [...hit.tests].sort(),
        outsideTest: hit.outsideTest,
      })),
      reasons: [...reasons].sort(),
    };
  }

  close(): void {
    delete process.env.OXIMUT_M3_COVERAGE;
    if (this.directory) rmSync(this.directory, { recursive: true, force: true });
    this.directory = undefined;
    this.recordFile = undefined;
  }
}
