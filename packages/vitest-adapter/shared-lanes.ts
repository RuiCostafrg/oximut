import { readdir, readFile } from 'node:fs/promises';
import { createRequire, getCompileCacheDir } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { setImmediate as waitForPoolBookkeeping } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { createVitest, ForksPoolWorker, ThreadsPoolWorker, version } from 'vitest/node';
import { envelope, type Request, type Response, type Result } from './protocol.ts';
import {
  type CpuSample,
  classifyCpu,
  cpuTicks,
  killTree,
  sampleWindowMs,
  type TimeoutKind,
} from './worker-liveness.ts';
import {
  fingerprint,
  type ProjectLike,
  type TransformService,
  TransformShare,
} from './transform-share.ts';

type Context = Awaited<ReturnType<typeof createVitest>>;
type LanePlan = {
  name: string;
  files: string[];
  selected: Awaited<ReturnType<Context['globTestSpecifications']>>;
};
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
 * Experimental rolling lanes run one stock Vitest pool per lane slot, all in
 * the same context, so a slot can start its next mutant the moment its previous
 * worker has exited. Vitest 5.1.0 does not export its pool factory; it lives in a
 * hashed chunk of the pinned package, found by its source and used only after
 * the version check. Without it the lanes run in barrier rounds instead.
 */
type LanePool = {
  runTests(
    specs: Awaited<ReturnType<Context['globTestSpecifications']>>,
    invalidates: string[],
  ): Promise<void>;
  close(): Promise<void>;
};
type PoolFactory = (ctx: Context) => LanePool;
let poolFactory: Promise<PoolFactory | undefined> | undefined;
function rollingPoolFactory(): Promise<PoolFactory | undefined> {
  poolFactory ??= (async () => {
    try {
      const chunks = new URL('./dist/chunks/', import.meta.resolve('vitest/package.json'));
      for (const name of await readdir(fileURLToPath(chunks))) {
        if (!/^index\..+\.js$/.test(name)) continue;
        const source = await readFile(join(fileURLToPath(chunks), name), 'utf8');
        const exported = /\bcreatePool as (\w+)\b/.exec(source)?.[1];
        if (!exported || !/\bfunction createPool\(ctx\) \{/.test(source)) continue;
        const factory = (await import(new URL(name, chunks).href))[exported];
        return typeof factory === 'function' ? (factory as PoolFactory) : undefined;
      }
    } catch {}
    return undefined;
  })();
  return poolFactory;
}

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

/** The slice of a lane worker's ChildProcess that killing it at its result uses. */
type LaneFork = {
  // Set by the zygote pool's stand-in for a child process (zygote/pool.cjs).
  oximutZygote?: true;
  exitCode: number | null;
  signalCode: string | null;
  kill(signal: string): boolean;
  once(event: 'exit', listener: () => void): unknown;
  pid?: number;
};

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
  // Lanes stopped at their deadline: whether the worker was on the CPU, for the
  // CLI's zygote watchdog (see worker-liveness.ts).
  private timeoutKinds = new Map<string, TimeoutKind>();
  private deadline = 0;
  // Rolling batches: each lane's own deadline, from the moment that lane starts.
  private laneDeadlines = new Map<string, number>();
  // Rolling batches: lane workers that have not exited yet. One that already gave
  // its result and was killed is `draining`: its slot no longer owns it.
  private liveForks = new Set<{ slot: string; draining: boolean }>();
  // Rolling batches: exits of killed workers whose slot has moved on (see fastExit).
  private exiting = new Set<Promise<void>>();
  private rolling = false;
  private threads = new Set<ThreadsPoolWorker>();
  private transforms = new TransformShare();
  private laneMutants = new Map<string, string>();
  // Template keys (JSON lists of test files) whose zygote exists or could not be built.
  private templates = new Map<string, boolean>();
  // The files the template being built collects.
  private warmFiles: string[] = [];
  // Test files (with their setup files) free of template-unsafe Vitest APIs.
  private safeFiles = new Map<string, boolean>();
  // Test files already checked to run in the Node environment.
  private nodeFiles = new Set<string>();

  private readonly templatePool?: TemplatePool;

  constructor(templatePool?: TemplatePool) {
    this.templatePool = templatePool;
  }

  async close() {
    const context = this.context;
    this.context = undefined;
    this.transforms.clear();
    this.dropTemplates();
    this.nodeFiles.clear();
    try {
      await context?.close();
    } finally {
      await this.drainThreads();
      await this.drainExits().catch(() => {});
    }
  }

  async run(batch: Request): Promise<Result> {
    if (version !== '5.1.0') throw new Error('Shared lanes require Vitest 5.1.0');
    const jobs = batch.lanes!;
    sample('batch_enter', { lanes: jobs.map((job) => job.mutation?.id) });
    const pool = batch.pool === 'threads' ? 'threads' : 'forks';
    // More lanes than the width: the lanes roll through the slots, each starting as
    // the previous worker of its slot has exited. Threads and unsupported Vitest
    // builds keep the barrier, one width-sized round after another.
    const slots = batch.workers ?? jobs.length;
    const rolling = jobs.length > slots;
    if (rolling && (pool !== 'forks' || !(await rollingPoolFactory())))
      return this.runInRounds(batch, slots);
    // Each lane has its own deadline (a mutant's is derived from the tests it runs),
    // counted from the batch's start in a barrier batch and from the lane's in a
    // rolling one. The batch's own deadline adds time to report after a lane is stopped.
    const started = performance.now();
    this.deadline = started + Math.max(...jobs.map((job) => job.deadlineMs));
    this.failures.clear();
    this.timeoutKinds.clear();
    this.files.clear();
    this.laneDeadlines.clear();
    if (!rolling)
      for (const [index, job] of jobs.entries())
        this.laneDeadlines.set(`oximut-lane-${index}`, started + job.deadlineMs);
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
        (existing.map !== active.map && JSON.stringify(existing.map) !== JSON.stringify(active.map))
      )
        throw new Error('Shared lanes require the resident instrumented module');
    }
    const changed = JSON.stringify([...modules]) !== JSON.stringify([...this.modules]);
    this.modules = modules;
    // A stable lane count keeps the context across a smaller final batch.
    const width = rolling ? slots : Math.max(slots, jobs.length);
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
                transform: (_code: string, id: string) => {
                  const module = this.modules.get(resolve(id.split('?')[0]));
                  if (module) return { code: module.code, map: JSON.stringify(module.map) };
                },
              },
            ],
            test: {
              name: `oximut-lane-${index}`,
              pool: {
                name: `oximut-lane-${pool}`,
                createPoolWorker: (options: ConstructorParameters<typeof ForksPoolWorker>[0]) => {
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
                  // Reads the worker's CPU time shortly before the deadline stops it.
                  let sampler: ReturnType<typeof setTimeout> | undefined;
                  const clearTimers = () => {
                    clearTimeout(timer);
                    clearTimeout(sampler);
                  };
                  let finished = false;
                  let stopping = false;
                  // Rolling lanes on zygote workers: the worker was killed at its result.
                  let killed = false;
                  const live = { slot: name, draining: false };
                  const messageListeners = new Set<(message: unknown) => void>();
                  const listen = worker.on.bind(worker);
                  const unlisten = worker.off.bind(worker);
                  worker.on = (event, callback) => {
                    if (event === 'message') messageListeners.add(callback);
                    listen(event, callback);
                  };
                  worker.off = (event, callback) => {
                    if (event === 'message') messageListeners.delete(callback);
                    unlisten(event, callback);
                  };
                  // The slot may hold its next mutant by the time this worker exits.
                  const mutant = this.laneMutants.get(name);
                  const note = trace
                    ? (event: string, extra?: Record<string, unknown>) =>
                        trace.emit('controller', event, { lane: name, mutant, ...extra })
                    : undefined;
                  worker.start = async () => {
                    note?.('lane_fork_requested');
                    await start();
                    note?.('lane_fork_returned');
                    if (worker instanceof ThreadsPoolWorker) this.threads.add(worker);
                    if (this.rolling) this.liveForks.add(live);
                    worker.on('exit', () => {
                      note?.('lane_exit');
                      this.liveForks.delete(live);
                      clearTimers();
                      if (!finished && !stopping && !this.failures.has(name))
                        this.failures.set(name, LANE_EXITED);
                    });
                  };
                  // Vitest stops an isolated worker the moment it has its result: it asks
                  // the worker to tear down, waits for its answer, sends SIGTERM and waits
                  // for the exit, and only then does the slot take its next mutant. Nothing
                  // the worker could still do changes the lane's verdict (every update it
                  // owed has been answered before it reports the file finished), so a
                  // zygote worker is killed at once and its exit is collected later, by
                  // drainExits(); the zygote still reaps it. Forks Vitest started itself
                  // and every failure path keep the stock stop.
                  const fastExit = () => {
                    if (!finished || !this.rolling) return false;
                    let fork: LaneFork;
                    try {
                      fork = (worker as unknown as { fork: LaneFork }).fork;
                    } catch {
                      return false;
                    }
                    if (!fork.oximutZygote || fork.exitCode !== null || fork.signalCode !== null)
                      return false;
                    stopping = true;
                    killed = true;
                    live.draining = true;
                    clearTimers();
                    const gone = new Promise<void>((resolve) => fork.once('exit', () => resolve()));
                    this.exiting.add(gone);
                    void gone.then(() => this.exiting.delete(gone));
                    fork.kill('SIGKILL');
                    note?.('lane_killed');
                    // The pool's stop waits for the worker's answer.
                    queueMicrotask(() => {
                      for (const listener of [...messageListeners])
                        listener({ type: 'stopped', __vitest_worker_response__: true });
                    });
                    return true;
                  };
                  worker.send = (message) => {
                    if (message.type === 'stop' && fastExit()) {
                      note?.('lane_stop_sent');
                      return;
                    }
                    // One pool per slot all number their first worker 1; a slot's
                    // number is its lane's, as in a single shared pool.
                    if (this.rolling && message.type === 'start')
                      (message as unknown as { poolId: number }).poolId = index + 1;
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
                      const delay = Math.max(
                        1,
                        (this.laneDeadlines.get(name) ?? this.deadline) - performance.now(),
                      );
                      const forkPid = () => {
                        try {
                          return (worker as unknown as { fork: LaneFork }).fork.pid;
                        } catch {
                          return undefined;
                        }
                      };
                      let before: CpuSample | undefined;
                      const window = sampleWindowMs(delay);
                      sampler = setTimeout(
                        () => {
                          const ticks = cpuTicks(forkPid());
                          before =
                            ticks === undefined ? undefined : { ticks, at: performance.now() };
                        },
                        Math.max(0, delay - window),
                      );
                      timer = setTimeout(() => {
                        this.failures.set(name, HARD_DEADLINE);
                        if (worker instanceof ThreadsPoolWorker) {
                          // terminate() also stops synchronous infinite loops. The pool
                          // still owns stop() and awaits exit before the next batch.
                          void (
                            worker as unknown as { thread: { terminate(): Promise<number> } }
                          ).thread.terminate();
                        } else {
                          const pid = forkPid();
                          const ticks = cpuTicks(pid);
                          const kind = classifyCpu(
                            before,
                            ticks === undefined ? undefined : { ticks, at: performance.now() },
                          );
                          if (kind) this.timeoutKinds.set(name, kind);
                          note?.('lane_deadline_kind', {
                            kind: kind ?? null,
                            pid: pid ?? null,
                            zygote: Boolean(
                              (worker as unknown as { fork?: LaneFork }).fork?.oximutZygote,
                            ),
                            before: before ?? null,
                            after: ticks === undefined ? null : { ticks, at: performance.now() },
                          });
                          // The worker's own children go with it, so that its memory and
                          // theirs are released now.
                          if (pid !== undefined) killTree(pid);
                          (worker as unknown as { fork: { kill(signal: string): void } }).fork.kill(
                            'SIGKILL',
                          );
                        }
                      }, delay);
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
                    clearTimers();
                    if (killed) return Promise.resolve();
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
    this.rolling = rolling;
    trace?.emit('controller', 'context_ready', {});
    for (const [index, project] of ctx.projects.entries()) {
      // Lanes beyond this batch stay idle: they receive no specification. A
      // rolling slot takes its mutant when it starts one.
      if (!rolling && jobs[index]) {
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
    if (rolling) return this.runRolling(batch, ctx, slots, specs, precollect);
    const representatives = [];
    const plans: LanePlan[] = [];
    for (const [index, job] of jobs.entries()) {
      const plan = await this.plan(specs, `oximut-lane-${index}`, job);
      this.files.set(plan.name, plan.files);
      plans.push(plan);
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
    const lanes = jobs.map((job, index) =>
      this.laneResult(job, index, result.testModules, unattributed, messageLimit),
    );
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
   * Rolling lanes: `slots` lane workers stay busy through all the batch's lanes.
   * Each slot owns a stock Vitest pool, so its next mutant starts as soon as its
   * previous worker has exited and the result is attributed, and nothing waits
   * for the slowest lane. A lane's own deadline starts when that lane does.
   */
  private async runRolling(
    batch: Request,
    ctx: Context,
    slots: number,
    specs: Awaited<ReturnType<Context['globTestSpecifications']>>,
    precollect: boolean,
  ): Promise<Result> {
    const jobs = batch.lanes!;
    const createPool = (await rollingPoolFactory())!;
    sample('rolling_begin', { lanes: jobs.length, slots });
    // Every lane's files are known now: an unsupported test file fails the batch
    // before anything starts, as in a barrier batch.
    const plans: LanePlan[] = [];
    for (const job of jobs) plans.push(await this.plan(specs, 'oximut-lane-0', job));
    // Each retained template is a live process. Keep the one with the most
    // planned reuse and send the other selections through ordinary fresh forks.
    const templates = new Map<string, Promise<boolean>>();
    let building: Promise<boolean> | undefined;
    if (precollect) {
      const candidates = new Map<string, { files: string[]; uses: number }>();
      for (const [position, job] of jobs.entries()) {
        const files = plans[position].files;
        const key = await this.templateCandidate(job, files, ctx.projects[0]);
        if (!key) continue;
        const candidate = candidates.get(key);
        if (candidate) candidate.uses++;
        else candidates.set(key, { files, uses: 1 });
      }
      let preferred: [string, { files: string[]; uses: number }] | undefined;
      for (const entry of candidates) {
        if (!preferred || entry[1].uses > preferred[1].uses) preferred = entry;
      }
      this.dropTemplatesExcept(new Set(preferred ? [preferred[0]] : []));
      if (preferred) {
        const [key, { files }] = preferred;
        building = this.warm(key, files).catch(() => false);
        templates.set(key, building);
      }
    }
    // Unhandled errors carry no project. One that arrives while lanes run taints
    // them all, as in a barrier batch; one that no lane can have caused (none
    // running) is not taken from any lane's result, but never goes unseen.
    const state = ctx.state;
    const active = new Set<string>();
    const tainted = new Set<string>();
    let stray = 0;
    const catchError = state.catchError.bind(state);
    state.catchError = (error, type) => {
      const before = state.getUnhandledErrors().length;
      catchError(error, type);
      if (state.getUnhandledErrors().length === before) return;
      if (active.size === 0) stray++;
      for (const name of active) tainted.add(name);
    };
    const results: Response[] = new Array(jobs.length);
    let next = 0;
    let fatal: unknown;
    const slot = async (index: number) => {
      const name = `oximut-lane-${index}`;
      const pool = createPool(ctx);
      try {
        while (fatal === undefined && next < jobs.length) {
          const position = next++;
          const job = jobs[position];
          try {
            results[position] = await this.runLane(
              ctx,
              pool,
              index,
              job,
              position,
              specs,
              templates,
              active,
              tainted,
            );
          } catch (error) {
            // Whatever went wrong, this lane's result is unknown and the slot
            // cannot be trusted; the others finish what they have started.
            fatal ??= error;
            active.delete(name);
            results[position] = envelope(job, {
              outcome: 'infrastructure-error',
              tests: { passed: 0, failed: 0, skipped: 0 },
              errors: [String((error as Error)?.message ?? error).slice(0, 2048)],
              ...(job.resident ? { resident: job.resident.length } : {}),
            });
          }
        }
      } finally {
        await pool.close().catch(() => {});
      }
    };
    try {
      await Promise.all(Array.from({ length: slots }, (_, index) => slot(index)));
      await building;
      // No killed worker may outlive the batch: its exit is what the CLI checks.
      await this.drainExits();
    } finally {
      state.catchError = catchError;
      this.laneDeadlines.clear();
      await building;
      // Pools of other slots may still be draining after a failure.
      for (const [position, job] of jobs.entries())
        results[position] ??= envelope(job, {
          outcome: 'infrastructure-error',
          tests: { passed: 0, failed: 0, skipped: 0 },
          errors: [`Rolling lanes stopped before this lane started: ${String(fatal)}`],
          ...(job.resident ? { resident: job.resident.length } : {}),
        });
    }
    if (fatal !== undefined) await this.close();
    else if (stray > 0)
      for (const result of results) {
        result.outcome = 'infrastructure-error';
        result.errors = ['Unattributed batch error', ...result.errors].slice(0, 8);
      }
    sample('rolling_end');
    return {
      outcome: 'skipped',
      tests: { passed: 0, failed: 0, skipped: 0 },
      errors: [],
      ...(batch.resident ? { resident: batch.resident.length } : {}),
      lanes: fitMessages(results),
    };
  }

  /** One lane job through its slot: start, run in a fresh worker, wait for its exit, attribute. */
  private async runLane(
    ctx: Context,
    pool: LanePool,
    index: number,
    job: Omit<Request, 'lanes'>,
    position: number,
    specs: Awaited<ReturnType<Context['globTestSpecifications']>>,
    templates: Map<string, Promise<boolean>>,
    active: Set<string>,
    tainted: Set<string>,
  ): Promise<Response> {
    const name = `oximut-lane-${index}`;
    const project = ctx.projects[index];
    const plan = await this.plan(specs, name, job);
    this.files.set(name, plan.files);
    this.failures.delete(name);
    this.timeoutKinds.delete(name);
    tainted.delete(name);
    // A template still being built is waited for; a lane never forks from one
    // that is not ready, and its own deadline has not started yet.
    const candidate = templates.size
      ? await this.templateCandidate(job, plan.files, project)
      : undefined;
    const ready = candidate ? await templates.get(candidate) : undefined;
    const { OXIMUT_ZYGOTE_TEMPLATE: _stale, ...env } = project.config.env ?? {};
    project.config.env = {
      ...env,
      OXIMUT_ACTIVE_MUTANT: job.mutation!.id,
      ...(ready ? { OXIMUT_ZYGOTE_TEMPLATE: candidate } : {}),
    };
    this.laneDeadlines.set(name, performance.now() + job.deadlineMs);
    if (trace) this.laneMutants.set(name, job.mutation!.id);
    // Vitest only clears the specifications it runs. Results of this slot's
    // previous mutant must not survive for the files of this one.
    ctx.state.clearFiles(project, plan.files);
    sample('lane_job_begin', { lane: name, mutant: job.mutation!.id, position });
    active.add(name);
    try {
      if (plan.selected.length) {
        try {
          await pool.runTests([plan.selected[0]], []);
        } catch (error) {
          // The exit of a worker we know stopped (deadline or crash) explains its
          // pool error. Any other error is unattributed, whatever its source.
          const failure = this.failures.get(name);
          const errors = error instanceof AggregateError ? error.errors : [error];
          const explained =
            (failure === LANE_EXITED || failure === HARD_DEADLINE) &&
            errors.length === 1 &&
            POOL_EXIT.test(String((errors[0] as Error)?.message));
          if (!explained) ctx.state.catchError(error, 'Unhandled Error');
        } finally {
          // The pool resolves a task before it forgets it, and a worker that
          // crashed leaves a task a cancel would wait on forever.
          await waitForPoolBookkeeping();
          await pool.close();
        }
        if ([...this.liveForks].some((fork) => fork.slot === name && !fork.draining))
          throw new Error('Rolling lane still owns a live fork after pool drain');
      }
    } finally {
      active.delete(name);
    }
    sample('lane_job_end', { lane: name, mutant: job.mutation!.id, position });
    return this.laneResult(job, index, ctx.state.getTestModules(), tainted.has(name), 2048);
  }

  /** Barrier fallback for more lanes than slots: `slots` lanes at a time. */
  private async runInRounds(batch: Request, slots: number): Promise<Result> {
    const jobs = batch.lanes!;
    const lanes: Response[] = [];
    let result: Result | undefined;
    for (let start = 0; start < jobs.length; start += slots) {
      result = await this.run({
        ...batch,
        workers: slots,
        lanes: jobs.slice(start, start + slots),
      });
      lanes.push(...(result.lanes ?? []));
    }
    return { ...result!, lanes };
  }

  /** One lane's response, from the test modules its project reported. */
  private laneResult(
    job: Omit<Request, 'lanes'>,
    index: number,
    testModules: Awaited<ReturnType<Context['runTestSpecifications']>>['testModules'],
    unattributed: boolean,
    messageLimit: number,
  ): Response {
    const name = `oximut-lane-${index}`;
    const modules = testModules.filter(
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
              : // Collection and hook errors thrown by the code under test fail like a test.
                tests.failed || moduleErrors.length
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
    // Why a timeout happened, for the CLI's zygote watchdog: the worker either
    // answered (Vitest's own test timeout) or was stopped at the deadline.
    const timeoutKind =
      outcome !== 'timeout'
        ? undefined
        : failure === HARD_DEADLINE
          ? this.timeoutKinds.get(name)
          : 'test-timeout';
    return envelope(job, {
      outcome,
      tests,
      errors: [...(untrusted ? [untrusted] : []), ...messages].slice(0, 8),
      ...(job.resident ? { resident: job.resident.length } : {}),
      ...(timeoutKind ? { timeoutKind } : {}),
    });
  }

  /** The files a lane's job selects in the lane's project. */
  private async plan(
    specs: Awaited<ReturnType<Context['globTestSpecifications']>>,
    name: string,
    job: Omit<Request, 'lanes'>,
  ): Promise<LanePlan> {
    const selected = specs.filter(
      (spec) =>
        spec.project.name === name &&
        (job.selection?.mode !== 'files' || job.selection.files.includes(resolve(spec.moduleId))),
    );
    for (const spec of selected) {
      if (this.nodeFiles.has(spec.moduleId)) continue;
      const source = await readFile(spec.moduleId, 'utf8');
      if (/@(?:vitest|jest)-environment\s+(?!node\b)/.test(source))
        throw new Error('Shared lanes require Node test files');
      this.nodeFiles.add(spec.moduleId);
    }
    return { name, files: selected.map((spec) => spec.moduleId), selected };
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
    job: Omit<Request, 'lanes'>,
    files: string[],
    project: Context['projects'][number],
  ): Promise<string | undefined> {
    const key = await this.templateCandidate(job, files, project);
    return key && (await this.warm(key, files)) ? key : undefined;
  }

  /** The template key a job could use, before any template is built for it. */
  private async templateCandidate(
    job: Omit<Request, 'lanes'>,
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
    return JSON.stringify(files);
  }

  private dropTemplates() {
    this.templates.clear();
    this.templatePool?.dropTemplates();
  }

  /** Drop every template whose key is not in `keep`, killing its zygote. */
  private dropTemplatesExcept(keep: Set<string>) {
    for (const key of [...this.templates.keys()]) {
      if (keep.has(key)) continue;
      this.templates.delete(key);
      this.templatePool?.dropTemplate(key);
    }
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

  /** Wait until every worker killed at its result has exited and been reaped. */
  private async drainExits(timeoutMs = 10_000) {
    if (!this.exiting.size) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const late = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('A killed lane worker did not exit')), timeoutMs);
    });
    try {
      await Promise.race([Promise.all([...this.exiting]), late]);
    } finally {
      clearTimeout(timer);
    }
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

/**
 * A response's error messages share one budget, so eight errors per lane
 * escaped stay well under the 1 MiB response limit however many lanes it holds.
 */
function fitMessages(lanes: Response[]): Response[] {
  const count = lanes.reduce((sum, lane) => sum + lane.errors.length, 0);
  const limit = Math.min(2048, Math.floor((128 * 1024) / Math.max(1, count)));
  for (const lane of lanes) lane.errors = lane.errors.map((message) => message.slice(0, limit));
  return lanes;
}

function isTransformService(value: unknown): value is TransformService {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { fetch?: unknown }).fetch === 'function'
  );
}
