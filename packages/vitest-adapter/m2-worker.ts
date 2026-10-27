import { resolve } from 'node:path';
import { createVitest, version, type Reporter } from 'vitest/node';
import type { Request, Result } from './protocol.ts';
import { SelectionSupport } from './m3-selection.ts';

const selectionSupport = new SelectionSupport();

let vitest: Awaited<ReturnType<typeof createVitest>> | undefined;
let currentRun: Parameters<NonNullable<Reporter['onTestRunEnd']>> | undefined;
let busy = false;
let mutation: Request['mutation'] = null;
let probes: Request['collectCoverage'];

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
async function command(name: string, args: Request): Promise<unknown> {
  if (name === 'initialize') {
    if (vitest) throw new Error('Already initialized');
    if (version !== '4.1.12') throw new Error(`Unsupported Vitest ${version}; expected 4.1.12`);
    mutation = args.mutation;
    probes = args.collectCoverage;
    delete process.env.OXIMUT_ACTIVE_MUTANT;
    if (args.kind === 'mutant' && mutation) process.env.OXIMUT_ACTIVE_MUTANT = mutation.id;
    vitest = await createVitest(
      'test',
      {
        root: args.root,
        config: args.config,
        watch: false,
        pool: 'forks',
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
              const probe = probes?.find(
                (probe) => resolve(id.split('?')[0]) === resolve(probe.file),
              );
              if (probe) {
                selectionSupport.observed.add(resolve(probe.file));
                return { code: probe.code, map: JSON.stringify(probe.map) };
              }
              if (mutation && resolve(id.split('?')[0]) === resolve(mutation.file)) {
                return { code: mutation.code, map: JSON.stringify(mutation.map) };
              }
            },
          },
        ],
      },
    );
    await vitest.standalone();
    if (
      vitest.projects.length !== 1 ||
      vitest.projects.some(
        (project) =>
          project.config.pool !== 'forks' ||
          project.config.isolate !== true ||
          project.config.maxWorkers !== 1 ||
          project.config.maxConcurrency !== 1 ||
          project.config.environment !== 'node' ||
          project.config.browser?.enabled === true,
      )
    )
      throw new Error(
        'Unsupported resource configuration: one Node project, one fork, sequential files and tests, and isolation are required',
      );
    if (
      vitest.projects.some(
        (project) =>
          project.config.pool !== 'forks' ||
          project.config.isolate !== true ||
          project.config.maxWorkers !== 1,
      )
    )
      throw new Error(
        'Unsupported project isolation configuration: forks, isolate and one worker are required',
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
  const job = (args as unknown as { request: Request }).request;
  const next = job.mutation;
  const probesChanged = JSON.stringify(probes) !== JSON.stringify(job.collectCoverage);
  if (probesChanged) {
    // Probe changes can affect every source file and attribution dependency.
    for (const project of vitest.projects) project.vite.moduleGraph.invalidateAll();
  } else if (
    mutation?.file !== next?.file ||
    mutation?.code !== next?.code ||
    JSON.stringify(mutation?.map) !== JSON.stringify(next?.map)
  ) {
    for (const project of vitest.projects) {
      // Restore the previous transform as well as activating the next one.
      // Vite invalidates every query/SSR variant and propagates to importers;
      // unrelated modules retain their transform cache. Test forks stay fresh.
      const files = new Set([mutation?.file, next?.file]);
      for (const file of files) {
        if (file) project.vite.moduleGraph.onFileChange(resolve(file));
      }
    }
  }
  mutation = next;
  probes = job.collectCoverage;
  currentRun = undefined;
  delete process.env.OXIMUT_ACTIVE_MUTANT;
  if (job.kind === 'mutant' && next) process.env.OXIMUT_ACTIVE_MUTANT = next.id;
  try {
    selectionSupport.begin(vitest, job);
    const specifications = await vitest.globTestSpecifications();
    const selected =
      job.selection?.mode === 'files'
        ? specifications.filter((spec) => job.selection?.files.includes(resolve(spec.moduleId)))
        : specifications;
    await vitest.runTestSpecifications(selected, true);
  } finally {
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
  let incomplete = reason === 'interrupted';
  for (const module of modules) {
    for (const test of module.children.allTests()) {
      const result = test.result();
      if (result.state === 'passed' || result.state === 'failed' || result.state === 'skipped')
        tests[result.state]++;
      else incomplete = true;
      if (result.state === 'failed') errors.push(...(result.errors ?? []));
    }
  }
  let outcome: Result['outcome'];
  const processFailure = errors.some((error) =>
    /process\.exit unexpectedly called/.test(String(error.message ?? '')),
  );
  const timedOut = errors.some((error) =>
    /(?:Test|Hook) timed out in \d+ms/.test(String(error.message ?? '')),
  );
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
