import { createVitest, version as vitestVersion } from 'vitest/node';
import { relative, resolve } from 'node:path';

let vitest;
let root;
let discovered;
let coverage;
let currentRun;
let busy = false;
let runNumber = 0;

const errorPayload = (error) => ({ name: error?.name ?? 'Error', message: error?.message ?? String(error) });
function testPayload(test) {
  const result = test.result();
  return {
    id: JSON.stringify([test.project.name ?? '', relative(root, test.module.moduleId).replaceAll('\\', '/'), test.id]),
    vitestId: test.id,
    project: test.project.name ?? '',
    file: relative(root, test.module.moduleId).replaceAll('\\', '/'),
    name: test.fullName,
    state: result.state,
    errors: (result.errors ?? []).map(errorPayload),
    meta: test.meta(),
    concurrent: test.options.concurrent === true,
  };
}
function summarize(result) {
  const tests = result.testModules.flatMap((module) => [...module.children.allTests()].map(testPayload));
  const errors = [
    ...result.unhandledErrors,
    ...result.testModules.flatMap((module) => [...module.errors(), ...[...module.children.allSuites()].flatMap((suite) => suite.errors())]),
  ].map(errorPayload);
  return { tests, errors };
}
function coveragePayload(map) {
  if (!map || typeof map !== 'object') throw new Error('Unexpected onCoverage payload');
  const data = typeof map.toJSON === 'function' ? map.toJSON() : map;
  const files = Object.entries(data).map(([file, item]) => {
    const value = item.data ?? item;
    if (!value.s || !value.f || !value.b) throw new Error(`Unexpected coverage entry for ${file}`);
    const counts = (entries) => ({ total: entries.length, covered: entries.filter((x) => x > 0).length });
    return {
      file: relative(root, file).replaceAll('\\', '/'),
      statements: counts(Object.values(value.s)),
      functions: counts(Object.values(value.f)),
      branches: counts(Object.values(value.b).flat()),
    };
  }).sort((a, b) => a.file.localeCompare(b.file));
  return { complete: true, granularity: 'whole-run', source: 'supported Reporter.onCoverage (V8 provider)', files };
}

async function command(name, args) {
  if (name === 'initialize') {
    if (vitest) throw new Error('Already initialized');
    if (vitestVersion !== '4.1.10') throw new Error(`Unsupported Vitest ${vitestVersion}; M0 requires 4.1.10`);
    root = resolve(args.root);
    const reporter = {
      onCoverage(map) { coverage = coveragePayload(map); },
      onTestRunEnd(testModules, unhandledErrors, reason) { currentRun = { testModules, unhandledErrors, reason }; },
    };
    vitest = await createVitest('test', {
      root,
      config: args.config === false ? false : resolve(args.config ?? `${root}/vitest.config.ts`),
      watch: false,
      pool: 'forks',
      maxWorkers: 1,
      fileParallelism: false,
      isolate: true,
      includeTaskLocation: true,
      reporters: [reporter],
      coverage: {
        enabled: args.coverage === true,
        provider: 'v8',
        include: ['src/**/*.{js,ts,mjs}'],
        reporter: [],
        reportsDirectory: resolve(root, '.vitest/oximut-coverage'),
        clean: true,
        cleanOnRerun: true,
        reportOnFailure: true,
      },
    });
    await vitest.standalone();
    return { versions: { node: process.version, vitest: vitestVersion }, controllerPid: process.pid };
  }
  if (!vitest) throw new Error('Not initialized');
  if (name === 'discover') {
    // Runtime collection handles dynamic/parameterized tests; it deliberately executes imports.
    const result = await vitest.collect(undefined, { staticParse: false });
    const payload = summarize(result);
    if (payload.errors.length || !payload.tests.length) throw new Error(`Discovery failed or empty: ${JSON.stringify(payload.errors)}`);
    discovered = new Map();
    for (const module of result.testModules) {
      for (const test of module.children.allTests()) {
        const id = testPayload(test).id;
        if (discovered.has(id)) throw new Error(`Duplicate identity ${id}`);
        discovered.set(id, { project: module.project, moduleId: module.moduleId, vitestId: test.id });
      }
    }
    return { ...payload, controllerPid: process.pid, discovery: 'runtime collection; imports execute, test callbacks do not' };
  }
  if (name === 'run') {
    if (!discovered) throw new Error('Discover tests before running');
    if (args.environment) {
      for (const [key, value] of Object.entries(args.environment)) {
        if (!/^OXIMUT_[A-Z_]+$/.test(key)) throw new Error(`Experiment environment key not allowed: ${key}`);
        if (value === null) delete process.env[key];
        else if (typeof value === 'string') process.env[key] = value;
        else throw new Error('Environment values must be strings or null');
      }
    }
    let specs;
    if (args.testIds !== undefined) {
      if (!Array.isArray(args.testIds) || args.testIds.length === 0 || new Set(args.testIds).size !== args.testIds.length) throw new Error('Selected test IDs must be a nonempty unique array');
      const groups = new Map();
      for (const id of args.testIds) {
        const known = discovered.get(id);
        if (!known) throw new Error(`Unknown test ID: ${id}`);
        const key = JSON.stringify([known.project.name, known.moduleId]);
        if (!groups.has(key)) groups.set(key, { ...known, ids: [] });
        groups.get(key).ids.push(known.vitestId);
      }
      specs = [...groups.values()].map(({ project, moduleId, ids }) => project.createSpecification(moduleId, { testIds: ids }));
    } else specs = await vitest.globTestSpecifications();
    coverage = undefined;
    currentRun = undefined;
    const started = performance.now();
    await vitest.runTestSpecifications(specs, args.testIds === undefined);
    if (!currentRun) throw new Error('Missing onTestRunEnd event');
    // runTestSpecifications can return historical modules too. Reporter
    // modules describe this job and prevent stale results from becoming evidence.
    const payload = summarize(currentRun);
    const executed = payload.tests.filter((test) => test.state !== 'skipped');
    if (args.testIds && (executed.length !== args.testIds.length || executed.some((test) => !args.testIds.includes(test.id)))) {
      throw new Error(`Selected test identity/result mismatch: requested=${JSON.stringify(args.testIds)} actual=${JSON.stringify(executed)}`);
    }
    const incomplete = currentRun.reason === 'interrupted' || payload.tests.some((test) => !['passed', 'failed', 'skipped'].includes(test.state));
    const outcome = payload.errors.length || incomplete ? 'infrastructure-error' : payload.tests.some((test) => test.state === 'failed') ? 'failed' : executed.length ? 'passed' : 'infrastructure-error';
    return { ...payload, outcome, runNumber: ++runNumber, controllerPid: process.pid, elapsedMs: performance.now() - started, coverage: coverage ?? { complete: false, reason: 'disabled or no coverage event' } };
  }
  if (name === 'close') {
    await vitest.close();
    return { closed: true };
  }
  throw new Error(`Unknown command: ${name}`);
}

process.on('message', async (message) => {
  if (!message || message.protocol !== 1 || !Number.isSafeInteger(message.id)) return;
  if (busy) return process.send({ protocol: 1, id: message.id, error: 'Controller is busy' });
  busy = true;
  try {
    const result = await command(message.command, message.args ?? {});
    process.send({ protocol: 1, id: message.id, result });
  } catch (error) {
    process.send({ protocol: 1, id: message.id, error: error?.stack ?? String(error) });
  } finally { busy = false; }
});
process.once('disconnect', () => process.exit(0));
