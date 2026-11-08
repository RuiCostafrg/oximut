import { readFile } from 'node:fs/promises';
import { getCompileCacheDir } from 'node:module';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createVitest, ForksPoolWorker, version } from 'vitest/node';
import { envelope, type Request, type Result } from './protocol.ts';

type Context = Awaited<ReturnType<typeof createVitest>>;
const runner = fileURLToPath(new URL('./lane-runner.mjs', import.meta.url));

/** Experimental pinned-runtime batch execution. No assignment survives pool drain. */
export class SharedLanes {
  private context?: Context;
  private width = 0;
  private modules = new Map<string, NonNullable<Request['mutation']>>();
  private files = new Map<string, string[]>();
  private failures = new Map<string, string>();
  private deadline = 0;

  async close() {
    await this.context?.close();
    this.context = undefined;
  }

  async run(batch: Request): Promise<Result> {
    if (version !== '4.1.12') throw new Error('Shared lanes require Vitest 4.1.12');
    const jobs = batch.lanes!;
    this.deadline = performance.now() + batch.deadlineMs;
    this.failures.clear();
    this.files.clear();
    const modules = new Map<string, NonNullable<Request['mutation']>>();
    for (const job of jobs) {
      // Active-only modules cannot be served under one URL in concurrent lanes.
      for (const module of job.resident ?? []) modules.set(resolve(module.file), module);
      const active = job.mutation!;
      const existing = modules.get(resolve(active.file));
      if (!existing)
        throw new Error('Shared lanes require a resident instrumented module for every mutant');
      if (
        existing.code !== active.code ||
        JSON.stringify(existing.map) !== JSON.stringify(active.map)
      )
        throw new Error('Lane mutation must use the resident instrumented module');
    }
    const changed = JSON.stringify([...modules]) !== JSON.stringify([...this.modules]);
    this.modules = modules;
    if (this.context && this.width !== jobs.length) await this.close();
    if (!this.context) {
      this.width = jobs.length;
      this.context = await createVitest('test', {
        root: batch.root,
        config: batch.config,
        watch: false,
        maxWorkers: jobs.length,
        maxConcurrency: 1,
        fileParallelism: true,
        isolate: true,
        bail: 0,
        coverage: { enabled: false },
        reporters: [],
        projects: jobs.map((_, index) => ({
          extends: true,
          plugins: [
            {
              name: 'oximut-shared-resident',
              enforce: 'pre',
              transform: (_code, id) => {
                const module = this.modules.get(resolve(id.split('?')[0]));
                if (module) return { code: module.code, map: JSON.stringify(module.map) };
              },
            },
          ],
          test: {
            name: `oximut-lane-${index}`,
            pool: {
              name: 'oximut-lane-forks',
              createPoolWorker: (options) => {
                const worker = new ForksPoolWorker(options);
                const start = worker.start.bind(worker);
                const stop = worker.stop.bind(worker);
                const send = worker.send.bind(worker);
                const deserialize = worker.deserialize.bind(worker);
                const name = options.project.name;
                let timer: ReturnType<typeof setTimeout> | undefined;
                let finished = false;
                let stopping = false;
                worker.start = async () => {
                  await start();
                  worker.on('exit', () => {
                    clearTimeout(timer);
                    if (!finished && !stopping)
                      this.failures.set(name, 'Lane worker exited before completion');
                  });
                };
                worker.send = (message) => {
                  if (message.type === 'run') {
                    // Pinned PoolRunner request shape. Send all selected Node files
                    // in one isolated task: exactly one fresh OS fork per mutant.
                    const run = message as unknown as {
                      context: { files: { filepath: string }[] };
                    };
                    run.context.files = this.files.get(name)!.map((filepath) => ({ filepath }));
                    timer = setTimeout(
                      () => {
                        this.failures.set(name, 'Lane hard deadline exceeded');
                        (worker as unknown as { fork?: { kill(signal: string): void } }).fork?.kill(
                          'SIGKILL',
                        );
                      },
                      Math.max(1, this.deadline - performance.now() - 250),
                    );
                  }
                  return send(message);
                };
                worker.deserialize = (data) => {
                  const message = deserialize(data);
                  const wire = message as { type?: string; m?: string };
                  if (wire?.type === 'testfileFinished') finished = true;
                  if (wire?.m === 'onUnhandledError')
                    this.failures.set(name, 'Unhandled lane worker error');
                  return message;
                };
                worker.stop = async () => {
                  stopping = true;
                  clearTimeout(timer);
                  await stop();
                };
                return worker;
              },
            },
            runner,
            isolate: true,
            bail: 0,
            maxWorkers: jobs.length,
            fileParallelism: true,
            maxConcurrency: 1,
          },
        })),
      });
      await this.context.standalone();
      if (getCompileCacheDir())
        for (const project of this.context.projects)
          project.config.execArgv = [
            ...project.config.execArgv,
            '--require',
            fileURLToPath(new URL('./compile-cache.cjs', import.meta.url)),
          ];
    } else if (changed) {
      for (const project of this.context.projects) project.vite.moduleGraph.invalidateAll();
    }
    const ctx = this.context;
    for (const [index, project] of ctx.projects.entries()) {
      project.config.env = {
        ...project.config.env,
        OXIMUT_ACTIVE_MUTANT: jobs[index].mutation!.id,
      };
      if (
        project.config.environment !== 'node' ||
        project.config.globalSetup?.length ||
        project.config.browser.enabled
      )
        throw new Error('Shared lanes require Node projects without global setup');
    }
    const specs = await new ctx.config.sequence.sequencer(ctx).sort(
      await ctx.globTestSpecifications(),
    );
    const representatives = [];
    for (const [index, job] of jobs.entries()) {
      const name = `oximut-lane-${index}`;
      const selected = specs.filter(
        (spec) =>
          spec.project.name === name &&
          (job.selection?.mode !== 'files' || job.selection.files.includes(resolve(spec.moduleId))),
      );
      for (const spec of selected) {
        const source = await readFile(spec.moduleId, 'utf8');
        if (/@(?:vitest|jest)-environment\s+(?!node\b)/.test(source))
          throw new Error('Shared lanes require Node test files');
      }
      this.files.set(
        name,
        selected.map((spec) => spec.moduleId),
      );
      if (selected.length) representatives.push(selected[0]);
    }
    const result = await ctx.runTestSpecifications(representatives, true);
    // runTestSpecifications does not await isolated stop(). Drain before reassigning.
    await (ctx as unknown as { pool: { close(): Promise<void> } }).pool.close();
    const lanes = jobs.map((job, index) => {
      const name = `oximut-lane-${index}`;
      const modules = result.testModules.filter(
        (module) => module.project.name === name && this.files.get(name)!.includes(module.moduleId),
      );
      const tests = { passed: 0, failed: 0, skipped: 0 };
      const moduleErrors = modules.flatMap((module) => [
        ...module.errors(),
        ...[...module.children.allSuites()].flatMap((suite) => suite.errors()),
      ]);
      const errors = [...moduleErrors];
      let pending = false;
      for (const module of modules)
        for (const test of module.children.allTests()) {
          const state = test.result();
          if (state.state === 'passed' || state.state === 'failed' || state.state === 'skipped')
            tests[state.state]++;
          else pending = true;
          errors.push(...(state.errors ?? []));
        }
      const messages = errors.map((error) => String(error.message ?? error));
      const failure =
        this.failures.get(name) ||
        (tests.failed && !errors.length ? 'Failed test without error evidence' : undefined);
      // Aggregate errors have lost project identity: conservatively taint all lanes.
      const untrusted =
        failure || (result.unhandledErrors.length ? 'Unattributed batch error' : undefined);
      const timedOut = messages.some((error) => /(?:Test|Hook) timed out in \d+ms/.test(error));
      const invalid = moduleErrors.some((error) =>
        /Transform failed|Parse failure|Failed to parse source|Unexpected token|Expected.*but found/.test(
          String(error.message),
        ),
      );
      let outcome: Result['outcome'] =
        untrusted || messages.some((m) => /process\.exit unexpectedly called/.test(m))
          ? 'infrastructure-error'
          : invalid
            ? 'invalid'
            : timedOut
              ? 'timeout'
              : moduleErrors.length
                ? 'infrastructure-error'
                : tests.failed
                  ? 'failed'
                  : pending
                    ? 'infrastructure-error'
                    : tests.passed
                      ? 'passed'
                      : 'skipped';
      if (
        outcome === 'passed' &&
        job.selection &&
        (tests.passed + tests.skipped !== job.selection.selectedTests ||
          tests.skipped !== (job.selection.expectedSkipped ?? 0))
      )
        outcome = 'infrastructure-error';
      return envelope(job, {
        outcome,
        tests,
        errors: [...(untrusted ? [untrusted] : []), ...messages].slice(0, 8),
        ...(job.resident ? { resident: job.resident.length } : {}),
      });
    });
    return {
      outcome: 'skipped',
      tests: { passed: 0, failed: 0, skipped: 0 },
      errors: [],
      ...(batch.resident ? { resident: batch.resident.length } : {}),
      lanes,
    };
  }
}
