import { readFile } from 'node:fs/promises';
import { getCompileCacheDir } from 'node:module';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizePath } from 'vite';
import { createVitest, version, type Reporter } from 'vitest/node';
import { validateRequest, type Request, type Result } from './protocol.ts';
import { SelectionSupport } from './m3-selection.ts';

const selectionSupport = new SelectionSupport();
const compileCacheFlush = fileURLToPath(new URL('./compile-cache.cjs', import.meta.url));

let vitest: Awaited<ReturnType<typeof createVitest>> | undefined;
let currentRun: Parameters<NonNullable<Reporter['onTestRunEnd']>> | undefined;
let busy = false;
let pool: 'forks' | 'threads' = 'forks';
let mutation: Request['mutation'] = null;
let probes: Request['collectCoverage'];
let resident: Request['resident'];
let servedModules = new Map<string, NonNullable<Request['mutation']>>();

/** Modules served outside probe runs: the active mutation overrides resident. */
function served(
  active: Request['mutation'],
  shared: Request['resident'],
): Map<string, NonNullable<Request['mutation']>> {
  const modules = new Map<string, NonNullable<Request['mutation']>>();
  for (const item of shared ?? []) modules.set(resolve(item.file), item);
  if (active) modules.set(resolve(active.file), active);
  return modules;
}
function same(a?: NonNullable<Request['mutation']>, b?: NonNullable<Request['mutation']>) {
  return a?.code === b?.code && JSON.stringify(a?.map) === JSON.stringify(b?.map);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
function transformError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const item = error as Record<string, unknown>;
  return (
    (typeof item.plugin === 'string' &&
      /(?:oxc|esbuild|import-analysis|oximut)/i.test(item.plugin)) ||
    /(?:Transform failed|Parse failure|Failed to parse source|Expected.*but found|Unexpected token)/i.test(
      String(item.message ?? ''),
    )
  );
}
/** Drain the pinned pool without closing Vite or losing controller transforms. */
async function awaitWorkerTermination(context: NonNullable<typeof vitest>, remainingMs: number) {
  // Vitest 4.1.12 createPool() hides its Pool in a closure. Its wrapper.close()
  // calls Pool.cancel(): it drains activeTasks, sharedRunners and exitPromises,
  // then resets cancellation so the same pool can run again. runTests() alone
  // does not await runner.stop(), including Worker.terminate() for threads.
  // Guard this version-specific handle; never acknowledge a job without it.
  const handle = (
    context as unknown as { pool?: { name?: unknown; close?: unknown; runTests?: unknown } }
  ).pool;
  if (
    handle?.name !== 'default' ||
    typeof handle.close !== 'function' ||
    typeof handle.runTests !== 'function'
  )
    throw new Error('Unsupported Vitest pool: missing default pool termination handle');
  const timeoutMs = Math.min(2000, remainingMs);
  if (timeoutMs <= 0) throw new Error('Job deadline exhausted before worker termination');
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      handle.close(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error('Timed out waiting for test-worker termination')),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function command(name: string, args: Request): Promise<unknown> {
  if (name === 'initialize') {
    if (vitest) throw new Error('Already initialized');
    if (version !== '4.1.12') throw new Error(`Unsupported Vitest ${version}; expected 4.1.12`);
    // The native coordinator sends requests directly; validate them here.
    validateRequest(args);
    pool = args.pool === 'threads' ? 'threads' : 'forks';
    mutation = args.mutation;
    probes = args.collectCoverage;
    resident = args.resident;
    servedModules = served(mutation, resident);
    delete process.env.OXIMUT_ACTIVE_MUTANT;
    if (args.kind === 'mutant' && mutation) process.env.OXIMUT_ACTIVE_MUTANT = mutation.id;
    vitest = await createVitest(
      'test',
      {
        root: args.root,
        config: args.config,
        watch: false,
        pool: args.pool === 'threads' ? 'threads' : 'forks',
        maxWorkers: 1,
        maxConcurrency: 1,
        fileParallelism: false,
        isolate: true,
        coverage: { enabled: false },
        reporters: [
          {
            onTestRunEnd(...run) {
              currentRun = run;
            },
          } satisfies Reporter,
        ],
      },
      {
        plugins: [
          {
            name: 'oximut-m2-mutation',
            enforce: 'pre',
            transform(_code, id) {
              const file = resolve(id.split('?')[0]);
              const probe = probes?.find((probe) => file === resolve(probe.file));
              if (probe) {
                selectionSupport.observed.add(resolve(probe.file));
                return { code: probe.code, map: JSON.stringify(probe.map) };
              }
              const module = servedModules.get(file);
              if (module) return { code: module.code, map: JSON.stringify(module.map) };
            },
          },
        ],
      },
    );
    await vitest.standalone();
    // Fresh test forks reuse compiled Vitest runtime code through Node's
    // compile cache (CLI-provided per-run directory). Forks are stopped by
    // signal, so a fork-only preload flushes the cache on Vitest's stop
    // request. V8 validates cached code against source and flags.
    if (getCompileCacheDir())
      for (const project of vitest.projects)
        project.config.execArgv = [...project.config.execArgv, '--require', compileCacheFlush];
    if (
      vitest.projects.length !== 1 ||
      vitest.projects.some(
        (project) =>
          project.config.pool !== pool ||
          project.config.isolate !== true ||
          project.config.maxWorkers !== 1 ||
          project.config.maxConcurrency !== 1 ||
          project.config.environment !== 'node' ||
          project.config.browser?.enabled === true,
      )
    )
      throw new Error(
        'Unsupported resource configuration: one Node project, one requested worker, sequential files and tests, and isolation are required',
      );
    if (
      vitest.projects.some(
        (project) =>
          project.config.pool !== pool ||
          project.config.isolate !== true ||
          project.config.maxWorkers !== 1,
      )
    )
      throw new Error(
        'Unsupported project isolation configuration: requested pool, isolate and one worker are required',
      );
    const multipleProjects = vitest.projects.length !== 1;
    const unsupported = vitest.projects.some(
      (project) =>
        Boolean(project.config.globalSetup?.length) ||
        multipleProjects ||
        project.vite.config.plugins.some(
          (plugin) =>
            !/^(?:vite:|vitest(?::|$)|oximut-m2-mutation$|builtin:|alias$)/.test(plugin.name),
        ),
    );
    return {
      versions: { node: process.version, vitest: version },
      controllerPid: process.pid,
      pool: vitest.projects[0].config.pool,
      reuse: unsupported
        ? `fresh: custom plugins, global setup, projects, or isolation configuration (${vitest.projects.flatMap((project) => project.vite.config.plugins.map((plugin) => plugin.name)).join(',')})`
        : 'controller',
    };
  }
  if (!vitest) throw new Error('Not initialized');
  if (name === 'close') {
    selectionSupport.close();
    await vitest.close();
    return { closed: true };
  }
  if (name !== 'run') throw new Error(`Unknown command: ${name}`);
  const started = performance.now();
  const job = (args as unknown as { request: Request }).request;
  validateRequest(job);
  if ((job.pool === 'threads' ? 'threads' : 'forks') !== pool)
    throw new Error('Pool cannot change after initialization');
  const next = job.mutation;
  const probesChanged = JSON.stringify(probes) !== JSON.stringify(job.collectCoverage);
  if (probesChanged || job.collectCoverage) {
    // Probe changes affect every source file and attribution dependency.
    // Repeated probes also need fresh transforms for this job's observed set.
    for (const project of vitest.projects) project.vite.moduleGraph.invalidateAll();
  } else {
    // Invalidate exactly the files whose served module changes, restoring the
    // previous transform as well as activating the next one. Vite invalidates
    // every query/SSR variant and propagates to importers; unrelated modules
    // retain their transform cache. A switched mutant serves its file's resident
    // module, so consecutive switched mutants invalidate nothing; the selector
    // is read at runtime by fresh test forks.
    const before = servedModules;
    const after = served(next, job.resident);
    const changed = [...new Set([...before.keys(), ...after.keys()])].filter(
      (file) => !same(before.get(file), after.get(file)),
    );
    for (const project of vitest.projects) {
      for (const file of changed) project.vite.moduleGraph.onFileChange(normalizePath(file));
    }
  }
  mutation = next;
  probes = job.collectCoverage;
  resident = job.resident;
  servedModules = served(mutation, resident);
  currentRun = undefined;
  delete process.env.OXIMUT_ACTIVE_MUTANT;
  if (job.kind === 'mutant' && next) process.env.OXIMUT_ACTIVE_MUTANT = next.id;
  const previousWorkers = vitest.projects.map((project) => project.config.maxWorkers);
  const previousBail = vitest.projects.map((project) => project.config.bail);
  const cancellations: string[] = [];
  // runFiles clears onCancel listeners before starting workers (4.1.12).
  // Observe the public cancellation entry point instead, scoped to this job.
  const cancelCurrentRun = vitest.cancelCurrentRun;
  vitest.cancelCurrentRun = function (reason) {
    cancellations.push(String(reason));
    return cancelCurrentRun.call(this, reason);
  };
  try {
    // Vitest 4.1.12 serializes project.config afresh for each worker start.
    for (const project of vitest.projects) {
      project.config.bail = job.kind === 'mutant' ? 1 : 0;
      // 4.1.12 groupSpecs reads this on every run; createPool calls
      // setMaxWorkers for that group. Isolation still gives each file a fresh runner.
      project.config.maxWorkers = job.kind === 'baseline' ? (job.workers ?? 1) : 1;
    }
    selectionSupport.begin(vitest, job);
    const specifications = await vitest.globTestSpecifications();
    const selected =
      job.selection?.mode === 'files'
        ? specifications.filter((spec) => job.selection?.files.includes(resolve(spec.moduleId)))
        : specifications;
    // Match the file pragma precedence and syntax in pinned Vitest 4.1.12.
    for (const spec of selected) {
      const content = await readFile(spec.moduleId, 'utf8');
      const environment =
        content.match(/@(?:vitest|jest)-environment\s+([\w-]+)\b/)?.[1] ??
        spec.project.config.environment;
      if (environment !== 'node')
        throw new Error(
          `Unsupported test environment ${environment} in ${spec.moduleId}; Node is required`,
        );
    }
    await vitest.runTestSpecifications(selected, true);
    await awaitWorkerTermination(vitest, job.deadlineMs - (performance.now() - started));
  } finally {
    vitest.cancelCurrentRun = cancelCurrentRun;
    for (const [index, project] of vitest.projects.entries()) {
      project.config.bail = previousBail[index];
      project.config.maxWorkers = previousWorkers[index];
    }
    delete process.env.OXIMUT_ACTIVE_MUTANT;
  }
  if (!currentRun) throw new Error('Missing onTestRunEnd event');
  const [modules, unhandled, reason] = currentRun as Parameters<
    NonNullable<Reporter['onTestRunEnd']>
  >;
  const tests = { passed: 0, failed: 0, skipped: 0 };
  const moduleErrors = modules.flatMap((module) => [
    ...module.errors(),
    ...[...module.children.allSuites()].flatMap((suite) => suite.errors()),
  ]);
  const errors = [...unhandled, ...moduleErrors];
  let pending = false;
  let hasTestError = false;
  for (const module of modules) {
    for (const test of module.children.allTests()) {
      const result = test.result();
      if (result.state === 'passed' || result.state === 'failed' || result.state === 'skipped')
        tests[result.state]++;
      else pending = true;
      if (result.state === 'failed') {
        hasTestError ||= Boolean(result.errors?.length);
        errors.push(...(result.errors ?? []));
      }
    }
  }
  let outcome: Result['outcome'];
  const processFailure = errors.some((error) =>
    /process\.exit unexpectedly called/.test(String(error.message ?? '')),
  );
  const timedOut = errors.some((error) =>
    /(?:Test|Hook) timed out in \d+ms/.test(String(error.message ?? '')),
  );
  // The reporter reason is 'interrupted', not the cancellation's 'test-failure'.
  // Require both signals and a real test error; all other error precedence stays intact.
  const bailed =
    job.kind === 'mutant' &&
    reason === 'interrupted' &&
    cancellations.length > 0 &&
    cancellations.every((reason) => reason === 'test-failure') &&
    tests.failed > 0 &&
    hasTestError;
  const incomplete = (reason === 'interrupted' || pending || cancellations.length > 0) && !bailed;
  if (unhandled.length || incomplete || processFailure) outcome = 'infrastructure-error';
  else if (moduleErrors.some(transformError)) outcome = 'invalid';
  else if (timedOut) outcome = 'timeout';
  else if (moduleErrors.length) outcome = 'infrastructure-error';
  else if (tests.failed) outcome = 'failed';
  else if (tests.passed) outcome = 'passed';
  else outcome = 'skipped';
  if (
    job.selection &&
    outcome === 'passed' &&
    (tests.passed + tests.failed + tests.skipped !== job.selection.selectedTests ||
      tests.skipped !== (job.selection.expectedSkipped ?? 0))
  ) {
    outcome = 'infrastructure-error';
    errors.push({ name: 'Error', message: 'Selected test inventory changed since baseline' });
  }
  const result: Result = {
    outcome,
    tests,
    errors: errors.slice(0, 8).map((error) => String(error.message ?? error).slice(0, 2048)),
  };
  // Acknowledge the resident modules this job served; the CLI rejects
  // executed results from adapters that ignore them.
  if (job.resident) result.resident = job.resident.length;
  result.coverage = selectionSupport.finish(vitest, job, modules, result);
  selectionSupport.close();
  return result;
}

process.on('message', async (input: unknown) => {
  const request = input as { protocol?: number; id?: number; command: string; args: Request };
  if (!request || request.protocol !== 1 || !Number.isSafeInteger(request.id)) return;
  if (busy) {
    process.send?.({ protocol: 1, id: request.id, error: 'Controller busy' });
    return;
  }
  busy = true;
  try {
    const result = await command(request.command, request.args);
    process.send?.({ protocol: 1, id: request.id, result });
  } catch (error) {
    process.send?.({ protocol: 1, id: request.id, error: message(error) });
  } finally {
    busy = false;
  }
});
process.once('disconnect', () => process.exit(0));
