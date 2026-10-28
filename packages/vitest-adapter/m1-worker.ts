import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createVitest, version, type Reporter } from 'vitest/node';
import type { Request, Result } from './protocol.ts';
import { SelectionSupport } from './m3-selection.ts';

const selectionSupport = new SelectionSupport();

let vitest: Awaited<ReturnType<typeof createVitest>> | undefined;
let currentRun: Parameters<NonNullable<Reporter['onTestRunEnd']>> | undefined;
let busy = false;
let job: Request;

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
    job = args;
    if (vitest) throw new Error('Already initialized');
    if (version !== '4.1.12') throw new Error(`Unsupported Vitest ${version}; expected 4.1.12`);
    const mutation = args.mutation;
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
            name: 'oximut-m1-mutation',
            enforce: 'pre',
            transform(_code, id) {
              const probe = job.collectCoverage?.find(
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
    return { versions: { node: process.version, vitest: version }, controllerPid: process.pid };
  }
  if (!vitest) throw new Error('Not initialized');
  if (name === 'close') {
    selectionSupport.close();
    await vitest.close();
    return { closed: true };
  }
  if (name !== 'run') throw new Error(`Unknown command: ${name}`);
  currentRun = undefined;
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
