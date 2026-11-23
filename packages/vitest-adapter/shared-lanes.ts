import { readFile } from 'node:fs/promises';
import { createRequire, getCompileCacheDir } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createVitest, ForksPoolWorker, ThreadsPoolWorker, version } from 'vitest/node';
import { envelope, type Request, type Result } from './protocol.ts';
import {
  fingerprint,
  type ProjectLike,
  type TransformService,
  TransformShare,
} from './transform-share.ts';

type Context = Awaited<ReturnType<typeof createVitest>>;
const runner = fileURLToPath(new URL('./lane-runner.mjs', import.meta.url));
// Opt-in diagnostic timeline (OXIMUT_TRACE_TIMELINE); see timeline.cjs.
const trace = process.env.OXIMUT_TRACE_TIMELINE
  ? (createRequire(import.meta.url)('./timeline.cjs') as {
      emit(role: string, event: string, fields?: Record<string, unknown>): void;
    })
  : undefined;
// Controller event-loop busy time, cumulative, sampled at batch boundaries: the
// controller serves every lane worker's module fetches and resolves, so its
// utilization over the mutation phase says whether it limits the lanes.
const sample = (event: string, fields: Record<string, unknown> = {}) => {
  if (!trace) return;
  const { active, idle } = performance.eventLoopUtilization();
  trace.emit('controller', event, { ...fields, eluActive: active, eluIdle: idle });
};
const WARM_PROJECT = 'oximut-warm';
// Vitest APIs whose effects a template cannot hand to a later run of the same
// file: they act on module registries or process-wide state at load time.
const TEMPLATE_UNSAFE =
  /\b(?:vi|vitest|jest)\s*\.\s*(?:mock|doMock|unmock|doUnmock|hoisted|resetModules|importActual|importMock|stubGlobal|stubEnv|useFakeTimers|setSystemTime|mockObject)\b/;
const HARD_DEADLINE = 'Job deadline exceeded';
const LANE_EXITED = 'Lane worker exited before completion';
// Vitest's unattributed report of a pool worker that exited.
const POOL_EXIT = /^\[vitest-pool\]: Worker oximut-lane-(?:forks|threads) emitted error\.$/;

/**
 * Node always terminates a Worker after its native `error` event. Report only
 * its following exit, like forks: Vitest removes its pool error listener after
 * the first notification, so forwarding both would throw in the controller.
 * RPC/unhandled test errors still use Vitest's normal reporting path.
 */
class ExitOnlyThreadsPoolWorker extends ThreadsPoolWorker {
  override async start() {
    await super.start();
    super.on('error', () => {});
  }

  override on(event: string, callback: Parameters<ThreadsPoolWorker['on']>[1]) {
    if (event !== 'error') super.on(event, callback);
  }

  override off(event: string, callback: Parameters<ThreadsPoolWorker['on']>[1]) {
    if (event !== 'error') super.off(event, callback);
  }
}

/** The slice of the zygote pool (zygote/pool.cjs) that precollection uses. */
export interface TemplatePool {
  templateReady(name: string): boolean;
  dropTemplate(name: string): void;
  dropTemplates(): void;
}

/** Fresh-worker batch execution. No assignment survives pool drain. */
export class SharedLanes {
  private context?: Context;
  private width = 0;
  private pool: 'forks' | 'threads' = 'forks';
  private modules = new Map<string, NonNullable<Request['mutation']>>();
  private files = new Map<string, string[]>();
  private failures = new Map<string, string>();
  private deadline = 0;
  private threads = new Set<ThreadsPoolWorker>();
  private transforms = new TransformShare();
  private laneMutants = new Map<string, string>();
  // Template keys (JSON lists of test files) whose zygote exists or could not be built.
  private templates = new Map<string, boolean>();
  // The files the template being built collects.
  private warmFiles: string[] = [];
  // Test files (with their setup files) free of template-unsafe Vitest APIs.
  private safeFiles = new Map<string, boolean>();

  private readonly templatePool?: TemplatePool;

  constructor(templatePool?: TemplatePool) {
    this.templatePool = templatePool;
  }

  async close() {
    const context = this.context;
    this.context = undefined;
    this.transforms.clear();
    this.dropTemplates();
    try {
      await context?.close();
    } finally {
      await this.drainThreads();
    }
  }

  async run(batch: Request): Promise<Result> {
    if (version !== '5.1.0') throw new Error('Shared lanes require Vitest 5.1.0');
    const jobs = batch.lanes!;
    sample('batch_enter', { lanes: jobs.map((job) => job.mutation?.id) });
    const pool = batch.pool === 'threads' ? 'threads' : 'forks';
    // The batch's own deadline adds time to report after a lane is stopped.
    this.deadline = performance.now() + jobs[0].deadlineMs;
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
        throw new Error('Shared lanes require the resident instrumented module');
    }
    const changed = JSON.stringify([...modules]) !== JSON.stringify([...this.modules]);
    this.modules = modules;
    // A stable lane count keeps the context across a smaller final batch.
    const width = Math.max(batch.workers ?? jobs.length, jobs.length);
    if (this.context && (this.width !== width || this.pool !== pool)) await this.close();
    // Precollect needs forks and the zygote pool; per-job eligibility comes later.
    const precollect =
      pool === 'forks' && !!this.templatePool && jobs.some((job) => job.precollect !== undefined);
    if (!this.context) {
      const context = await createVitest('test', {
        root: batch.root,
        config: batch.config,
        watch: false,
        maxWorkers: width,
        maxConcurrency: 1,
        fileParallelism: true,
        isolate: true,
        bail: 0,
        coverage: { enabled: false },
        reporters: [],
        // Mutation transforms vary without changing source bytes; disk cache keys do not.
        fsModuleCache: false,
        projects: [
          ...Array.from({ length: width }, (_, index) => ({
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
                name: `oximut-lane-${pool}`,
                createPoolWorker: (options) => {
                  const worker =
                    pool === 'threads'
                      ? new ExitOnlyThreadsPoolWorker(options)
                      : new ForksPoolWorker(options);
                  const start = worker.start.bind(worker);
                  const stop = worker.stop.bind(worker);
                  const send = worker.send.bind(worker);
                  const deserialize = worker.deserialize.bind(worker);
                  const name = options.project.name;
                  let timer: ReturnType<typeof setTimeout> | undefined;
                  let finished = false;
                  let stopping = false;
                  const note = trace
                    ? (event: string) =>
                        trace.emit('controller', event, {
                          lane: name,
                          mutant: this.laneMutants.get(name),
                        })
                    : undefined;
                  worker.start = async () => {
                    note?.('lane_fork_requested');
                    await start();
                    note?.('lane_fork_returned');
                    if (worker instanceof ThreadsPoolWorker) this.threads.add(worker);
                    worker.on('exit', () => {
                      note?.('lane_exit');
                      clearTimeout(timer);
                      if (!finished && !stopping && !this.failures.has(name))
                        this.failures.set(name, LANE_EXITED);
                    });
                  };
                  worker.send = (message) => {
                    if (message.type === 'start') note?.('lane_start_sent');
                    if (message.type === 'stop') note?.('lane_stop_sent');
                    if (message.type === 'run') {
                      note?.('lane_run_sent');
                      // Pinned PoolRunner request shape. Send all selected Node files
                      // in one isolated task: exactly one fresh worker per mutant.
                      const run = message as unknown as {
                        context: { files: { filepath: string }[] };
                      };
                      run.context.files = this.files.get(name)!.map((filepath) => ({ filepath }));
                      timer = setTimeout(
                        () => {
                          this.failures.set(name, HARD_DEADLINE);
                          if (worker instanceof ThreadsPoolWorker) {
                            // terminate() also stops synchronous infinite loops. The pool
                            // still owns stop() and awaits exit before the next batch.
                            void (
                              worker as unknown as { thread: { terminate(): Promise<number> } }
                            ).thread.terminate();
                          } else {
                            (
                              worker as unknown as { fork: { kill(signal: string): void } }
                            ).fork.kill('SIGKILL');
                          }
                        },
                        Math.max(1, this.deadline - performance.now()),
                      );
                    }
                    return send(message);
                  };
                  worker.deserialize = (data) => {
                    const message = deserialize(data);
                    const wire = message as { type?: string; m?: string };
                    if (wire?.type === 'started') note?.('lane_started');
                    if (wire?.type === 'stopped') note?.('lane_stopped');
                    if (wire?.type === 'testfileFinished') {
                      note?.('lane_result');
                      finished = true;
                    }
                    if (wire?.m === 'onUnhandledError')
                      this.failures.set(name, 'Unhandled lane worker error');
                    return message;
                  };
                  let stopped: Promise<void> | undefined;
                  worker.stop = () => {
                    stopping = true;
                    clearTimeout(timer);
                    if (!(worker instanceof ThreadsPoolWorker)) return stop();
                    stopped ??= stop().then(() => {
                      this.threads.delete(worker);
                    });
                    return stopped;
                  };
                  return worker;
                },
              },
              runner,
              isolate: true,
              bail: 0,
              maxWorkers: width,
              fileParallelism: true,
              maxConcurrency: 1,
            },
          })),
          ...(precollect ? [this.warmProject()] : []),
        ],
      });
      try {
        await context.standalone();
      } catch (error) {
        await context.close();
        throw error;
      }
      this.context = context;
      this.width = width;
      this.pool = pool;
      if (getCompileCacheDir())
        for (const project of context.projects)
          project.config.execArgv = [
            ...project.config.execArgv,
            '--require',
            fileURLToPath(new URL('./compile-cache.cjs', import.meta.url)),
          ];
      // A new context owns new servers, module graphs and transform services.
      this.transforms.clear();
      this.shareTransforms(context, batch.root);
      trace?.emit('controller', 'context_created', { width });
    } else if (changed) {
      // The resident code changed: no lane may serve a previous batch's result,
      // and no template holds the new modules.
      this.transforms.clear();
      this.dropTemplates();
      for (const project of this.context.projects) project.vite.moduleGraph.invalidateAll();
    }
    const ctx = this.context;
    trace?.emit('controller', 'context_ready', {});
    for (const [index, project] of ctx.projects.entries()) {
      // Lanes beyond this batch stay idle: they receive no specification.
      if (jobs[index]) {
        project.config.env = {
          ...project.config.env,
          OXIMUT_ACTIVE_MUTANT: jobs[index].mutation!.id,
        };
        if (trace) this.laneMutants.set(`oximut-lane-${index}`, jobs[index].mutation!.id);
      }
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
    const plans: { name: string; files: string[]; selected: typeof specs }[] = [];
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
      const files = selected.map((spec) => spec.moduleId);
      this.files.set(name, files);
      plans.push({ name, files, selected });
    }
    for (const [index, job] of jobs.entries()) {
      const project = ctx.projects[index];
      const { OXIMUT_ZYGOTE_TEMPLATE: _stale, ...env } = project.config.env;
      // Experimental precollect: a lane whose selected test files have a warm
      // template forks its worker from that template (zygote/pool.cjs).
      const template = precollect
        ? await this.templateKey(job, plans[index].files, project)
        : undefined;
      project.config.env = template ? { ...env, OXIMUT_ZYGOTE_TEMPLATE: template } : env;
    }
    for (const [index, { files, selected }] of plans.entries()) {
      // Vitest only clears representatives. Results of a previous batch must not
      // survive for the other files of this one.
      ctx.state.clearFiles(ctx.projects[index], files);
      if (selected.length) representatives.push(selected[0]);
    }
    let result: Awaited<ReturnType<Context['runTestSpecifications']>>;
    sample('pool_run_begin');
    try {
      result = await ctx.runTestSpecifications(representatives, true);
      sample('pool_run_end');
      // runTestSpecifications does not await isolated stop(). Drain before reassigning.
      const pool = (ctx as unknown as { pool?: { close?: () => Promise<void> } }).pool;
      if (typeof pool?.close !== 'function')
        throw new Error('Unsupported Vitest pool: missing termination handle');
      await pool.close();
      trace?.emit('controller', 'pool_closed', {});
      // An unexpected exit marks the runner stopped, so Vitest may skip stop().
      // Still drain thread stdio and termination before returning/reassigning.
      await this.drainThreads();
    } catch (error) {
      await this.close();
      throw error;
    }
    // Eight errors per lane, escaped, must stay well under the 1 MiB response limit.
    const messageLimit = Math.min(2048, Math.floor((128 * 1024) / (jobs.length * 8)));
    // Each lane exit we observed explains one such error. Anything else, or any
    // more of them, cannot be attributed and still taints the whole batch.
    const exits = [...this.failures.values()].filter(
      (failure) => failure === LANE_EXITED || failure === HARD_DEADLINE,
    ).length;
    const poolExits = result.unhandledErrors.filter((error) =>
      POOL_EXIT.test(String((error as Error).message)),
    ).length;
    const unattributed = result.unhandledErrors.length > poolExits || poolExits > exits;
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
      // After a local bail the remaining files may never report; a failure stands.
      let pending =
        modules.length !== this.files.get(name)!.length ||
        modules.some((module) => module.state() === 'pending' || module.state() === 'queued');
      for (const module of modules)
        for (const test of module.children.allTests()) {
          const state = test.result();
          if (state.state === 'passed' || state.state === 'failed' || state.state === 'skipped')
            tests[state.state]++;
          else pending = true;
          errors.push(...(state.errors ?? []));
        }
      const messages = errors.map((error) => String(error.message ?? error).slice(0, messageLimit));
      const failure =
        this.failures.get(name) ||
        (tests.failed && !errors.length ? 'Failed test without error evidence' : undefined);
      // Aggregate errors have lost project identity: conservatively taint all lanes.
      const untrusted = failure || (unattributed ? 'Unattributed batch error' : undefined);
      const timedOut = messages.some((error) => /(?:Test|Hook) timed out in \d+ms/.test(error));
      const invalid = moduleErrors.some((error) =>
        /Transform failed|Parse failure|Failed to parse source|Unexpected token|Expected.*but found/.test(
          String(error.message),
        ),
      );
      let outcome: Result['outcome'] =
        failure === HARD_DEADLINE && !unattributed
          ? 'timeout'
          : untrusted || messages.some((m) => /process\.exit unexpectedly called/.test(m))
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
    sample('batch_done');
    return {
      outcome: 'skipped',
      tests: { passed: 0, failed: 0, skipped: 0 },
      errors: [],
      ...(batch.resident ? { resident: batch.resident.length } : {}),
      lanes,
    };
  }

  /**
   * The hidden project whose pool worker becomes a template (see warm()). Its
   * environment never names a mutant, so the template evaluates every module
   * with all mutations off.
   */
  private warmProject() {
    return {
      extends: true,
      plugins: [
        {
          name: 'oximut-shared-resident',
          enforce: 'pre' as const,
          transform: (_code: string, id: string) => {
            const module = this.modules.get(resolve(id.split('?')[0]));
            if (module) return { code: module.code, map: JSON.stringify(module.map) };
          },
        },
      ],
      test: {
        name: WARM_PROJECT,
        pool: {
          name: 'oximut-lane-forks',
          createPoolWorker: (options: ConstructorParameters<typeof ForksPoolWorker>[0]) => {
            const worker = new ForksPoolWorker(options);
            const send = worker.send.bind(worker);
            const on = worker.on.bind(worker);
            const off = worker.off.bind(worker);
            const listeners = new Set<(message: unknown) => void>();
            worker.on = (event: string, callback: never) => {
              if (event === 'message') listeners.add(callback);
              on(event, callback);
            };
            worker.off = (event: string, callback: never) => {
              listeners.delete(callback);
              off(event, callback);
            };
            const stop = worker.stop.bind(worker);
            // False when the zygote was unavailable and Vitest got a real process.
            const kept = () =>
              (worker as unknown as { _fork?: { oximutTemplate?: boolean } })._fork
                ?.oximutTemplate === true;
            worker.send = (message) => {
              const wire = message as unknown as { type: string };
              // Collect only: no test ever runs in a template. All its files go in
              // one task, as a lane's do.
              if (wire.type === 'run') {
                wire.type = 'collect';
                (wire as unknown as { context: { files: { filepath: string }[] } }).context.files =
                  this.warmFiles.map((filepath) => ({ filepath }));
              } else if (wire.type === 'stop' && kept()) {
                // Vitest's teardown would strip the zygote's message handlers: the
                // worker stays up, as the template, and only the pool is told.
                setImmediate(() => {
                  for (const callback of [...listeners])
                    callback({ type: 'stopped', __vitest_worker_response__: true });
                });
                return;
              }
              return send(message);
            };
            worker.stop = async () => {
              listeners.clear();
              if (!kept()) await stop();
            };
            return worker;
          },
        },
        runner,
        isolate: true,
        bail: 0,
        maxWorkers: 1,
        fileParallelism: true,
        maxConcurrency: 1,
      },
    };
  }

  /**
   * The template key (the JSON list of test files) a job's worker is forked from,
   * if any. Selections that are not whole test files, or whose files use Vitest
   * APIs a template cannot hand over, keep the ordinary zygote path.
   */
  private async templateKey(
    job: Request,
    files: string[],
    project: Context['projects'][number],
  ): Promise<string | undefined> {
    if (job.precollect !== true || job.selection?.mode !== 'files' || files.length === 0)
      return undefined;
    for (const file of [...files, ...project.config.setupFiles]) {
      let safe = this.safeFiles.get(file);
      if (safe === undefined) {
        safe = !TEMPLATE_UNSAFE.test(await readFile(file, 'utf8'));
        this.safeFiles.set(file, safe);
      }
      if (!safe) return undefined;
    }
    const key = JSON.stringify(files);
    return (await this.warm(key, files)) ? key : undefined;
  }

  private dropTemplates() {
    this.templates.clear();
    this.templatePool?.dropTemplates();
  }

  /**
   * Experimental precollect: run the zygote of one set of test files as a Vitest
   * worker through the warm project, so it fetches, evaluates and collects the
   * files once with no mutant active. The worker is then kept as the template
   * instead of being stopped (see zygote/pool.cjs).
   */
  private async warm(key: string, files: string[]): Promise<boolean> {
    const known = this.templates.get(key);
    if (known !== undefined) return known;
    const ctx = this.context!;
    const project = ctx.projects.find((candidate) => candidate.name === WARM_PROJECT);
    let ready = false;
    const built = trace ? performance.now() : 0;
    trace?.emit('controller', 'template_build_begin', { key });
    if (project && this.templatePool) {
      // The controller's own environment can name a mutant: the template has none.
      project.config.env = {
        ...project.config.env,
        OXIMUT_ZYGOTE_TEMPLATE: key,
        OXIMUT_ZYGOTE_WARM: '1',
        OXIMUT_ACTIVE_MUTANT: '',
      };
      this.warmFiles = files;
      try {
        const spec = (await ctx.globTestSpecifications()).find(
          (candidate) =>
            candidate.project.name === WARM_PROJECT && resolve(candidate.moduleId) === files[0],
        );
        if (spec) {
          await ctx.runTestSpecifications([spec], true);
          const pool = (ctx as unknown as { pool?: { close?: () => Promise<void> } }).pool;
          await pool?.close?.();
          ready = this.templatePool.templateReady(key);
        }
      } catch {
        ready = false;
      }
      if (!ready) this.templatePool.dropTemplate(key);
    }
    this.templates.set(key, ready);
    trace?.emit('controller', 'template_build_end', {
      key,
      ready,
      ms: performance.now() - built,
    });
    return ready;
  }

  private async drainThreads() {
    await Promise.all([...this.threads].map((worker) => worker.stop()));
  }

  /**
   * Give every lane project the same transform cache. Lanes whose configs can
   * change transformed output must not share, so a missing service, a shared
   * service instance or an unserializable config leaves sharing off entirely.
   */
  private shareTransforms(context: Context, root: string): void {
    const services: TransformService[] = [];
    const seen = new Set<TransformService>();
    for (const project of context.projects) {
      const service = (project as unknown as { _transformService?: unknown })._transformService;
      if (!isTransformService(service) || seen.has(service)) return;
      seen.add(service);
      services.push(service);
    }
    let names: string[];
    try {
      names = context.projects.map((project) => fingerprint(project as unknown as ProjectLike));
    } catch {
      return;
    }
    // lane-runner.mjs lives outside the project root but is transformed per lane too.
    const allowed = [resolve(root), dirname(runner)];
    for (const [index, service] of services.entries())
      this.transforms.wrap(service, names[index], allowed);
  }
}

function isTransformService(value: unknown): value is TransformService {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { fetch?: unknown }).fetch === 'function'
  );
}
