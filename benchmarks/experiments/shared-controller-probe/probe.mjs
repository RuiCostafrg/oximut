import { createVitest, ForksPoolWorker, version } from 'vitest/node';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { writeFile } from 'node:fs/promises';

const here = fileURLToPath(new URL('.', import.meta.url));
export const mutants = ['killed', 'survived', 'timeout', 'survived-again'];

export async function run(ids, workers = 2, { stock = false, mode = 'normal' } = {}) {
  if (version !== '4.1.12') throw new Error(`Expected Vitest 4.1.12, got ${version}`);
  const previousExitCode = process.exitCode;
  const events = [];
  const workerFailures = {};
  let markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  const waiters = [];
  async function acquire() {
    if (active >= workers) await new Promise(resolve => waiters.push(resolve));
    else ++active;
  }
  function release() {
    const next = waiters.shift();
    if (next) next();
    else --active;
  }
  let active = 0, peak = 0;
  // Exported experimental pool extension; no copied Vitest internals.
  const pool = {
    name: 'probe-forks',
    createPoolWorker(options) {
      const worker = new ForksPoolWorker(options);
      const start = worker.start.bind(worker), stop = worker.stop.bind(worker);
      const send = worker.send.bind(worker), deserialize = worker.deserialize.bind(worker);
      let deadline, finished = false, stopping = false, released = false;
      function releaseOnce() {
        if (released) return;
        released = true;
        clearTimeout(deadline);
        release();
        events.push({ type: 'stop', mutant: options.project.name });
      }
      worker.send = message => {
        if (message.type === 'run') {
          events.push({ type: 'run', mutant: options.project.name });
          markStarted();
          if (options.project.name === 'hard-timeout') deadline = setTimeout(() => {
            workerFailures[options.project.name] = 'timeout';
            events.push({ type: 'deadline', mutant: options.project.name });
            worker.fork.kill('SIGKILL');
          }, 3000);
        }
        send(message);
      };
      worker.deserialize = data => {
        const message = deserialize(data);
        if (message?.type === 'testfileFinished') finished = true;
        if (message?.m === 'onUnhandledError') {
          workerFailures[options.project.name] = 'unhandled';
          events.push({ type: 'unhandled', mutant: options.project.name, message: message.a?.[0]?.message });
        }
        return message;
      };
      worker.start = async () => {
        await acquire();
        try { await start(); } catch (error) { release(); throw error; }
        worker.on('exit', code => {
          if (!finished && !stopping) {
            workerFailures[options.project.name] ??= 'crash';
            events.push({ type: 'unexpected-exit', mutant: options.project.name, code });
          }
          releaseOnce();
        });
        peak = Math.max(peak, active);
        events.push({ type: 'start', mutant: options.project.name, pid: worker.fork.pid });
      };
      worker.stop = async () => {
        stopping = true;
        clearTimeout(deadline);
        await stop();
        releaseOnce();
      };
      return worker;
    },
  };
  const ctx = await createVitest('test', {
    root: here, config: false, watch: false, maxWorkers: workers,
    bail: 0, reporters: [{
      onTestRunStart() { events.push({ type: 'run-start' }); },
      onTestRunEnd() { events.push({ type: 'run-end' }); },
    }],
    projects: ids.map(id => ({
      test: {
        name: id, root: resolve(here, 'fixture'), include: ['shared.test.mjs'],
        pool: stock ? 'forks' : pool, isolate: true,
        env: { OXIMUT_ACTIVE_MUTANT: id }, provide: { mutant: id },
        runner: resolve(here, 'local-runner.mjs'),
        testTimeout: 500, maxConcurrency: 1,
      },
    })),
  });
  try {
    await ctx.standalone();
    const specs = await ctx.globTestSpecifications();
    const taskIds = specs.map(spec => spec.taskId);
    let result, perCallModules;
    if (mode === 'simultaneous') {
      const results = await Promise.all(specs.map(spec => ctx.runTestSpecifications([spec])));
      perCallModules = results.map(result => result.testModules.map(module => module.project.name));
      result = results.at(-1);
    } else if (mode === 'duplicate') result = await ctx.runTestSpecifications([specs[0], specs[0]]);
    else if (mode === 'overlap') {
      const first = ctx.runTestSpecifications(specs);
      await started;
      const second = ctx.runTestSpecifications(specs);
      await first;
      result = await second;
    } else result = await ctx.runTestSpecifications(specs);
    await ctx.pool.close(); // stock scheduling does not await isolated stop()
    const outcomes = Object.fromEntries(result.testModules.map(module => {
      const tests = [...module.children.allTests()];
      const states = tests.map(test => test.result().state);
      const errors = tests.flatMap(test => test.result().errors ?? []).map(error => error.message);
      const outcome = errors.some(error => /timed out/i.test(error)) ? 'timeout'
        : states.includes('failed') ? 'killed' : 'survived';
      return [module.project.name, { outcome: workerFailures[module.project.name] ?? outcome, total: tests.length,
        passed: states.filter(s => s === 'passed').length,
        failed: states.filter(s => s === 'failed').length,
        notRun: states.filter(s => !['passed', 'failed'].includes(s)).length, states }];
    }));
    return { outcomes, errors: result.unhandledErrors, events, peak, taskIds, workerFailures, perCallModules };
  } finally { await ctx.close(); process.exitCode = previousExitCode; }
}

if (process.argv[2] === '--oracle') {
  const result = await run([process.argv[3]], 1, { stock: true });
  await writeFile(process.argv[4], JSON.stringify(result));
  process.exitCode = 0;
}
