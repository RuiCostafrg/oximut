import { fork } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const controllerFile = fileURLToPath(new URL('./worker.mjs', import.meta.url));
const supervisorFile = fileURLToPath(new URL('./supervisor.py', import.meta.url));

/** An empty follow-up scan alone cannot prove an observed child disappeared. */
export async function hasUnexpectedDescendants(scan, check) {
  const observed = await scan();
  if (observed.length === 0) return false;
  if ((await scan()).length > 0) return true;
  for (const pid of observed) {
    try {
      await check(pid);
      return true;
    } catch (error) {
      if (!['ENOENT', 'ESRCH'].includes(error.code)) return true;
    }
  }
  return false;
}

async function within(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    })]);
  } finally { clearTimeout(timer); }
}

/** A separate controller process owns Vitest; stdout/stderr never carry IPC. */
export async function openController(options) {
  const openingStarted = performance.now();
  if (process.platform !== 'linux') throw new Error('M0 deadline/process-tree verification requires Linux');
  const temporary = await mkdtemp(join(tmpdir(), 'oximut-controller-'));
  const cleanupPath = join(temporary, 'cleanup.json');
  const child = fork(options.workerFile ?? controllerFile, [cleanupPath], {
    detached: true,
    execPath: 'python3',
    execArgv: [supervisorFile, process.execPath],
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
    env: { ...process.env, ...options.environment },
  });
  let nextId = 0;
  let closed = false;
  let active = false;
  let output = '';
  let truncated = false;
  let cleanup;
  const pending = new Map();
  const exited = new Promise((resolve) => child.once('exit', (code, signal) => {
    closed = true;
    for (const request of pending.values()) request.reject(new Error(`Controller exited (${code ?? signal})`));
    pending.clear();
    resolve({ code, signal });
  }));
  child.on('error', (error) => {
    for (const request of pending.values()) request.reject(error);
    pending.clear();
  });
  const capture = (chunk) => {
    if (output.length < 32768) output += chunk.toString().slice(0, 32768 - output.length);
    else truncated = true;
  };
  child.stdout.on('data', capture);
  child.stderr.on('data', capture);
  child.on('message', (message) => {
    if (!message || message.protocol !== 1 || !Number.isSafeInteger(message.id)) return;
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    if (message.error) request.reject(new Error(message.error));
    else request.resolve(message.result);
  });
  const request = (command, args = {}) => {
    if (closed) return Promise.reject(new Error('Controller is closed'));
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      child.send({ protocol: 1, id, command, args }, (error) => {
        if (error) { pending.delete(id); reject(error); }
      });
    });
  };
  async function terminate() {
    const started = performance.now();
    // Do not deliver SIGTERM before Python has installed its cleanup handler.
    // A startup-inclusive deadline can expire before the supervisor starts.
    while (!closed) {
      try { await readFile(`${cleanupPath}.ready`); break; }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (performance.now() - started >= 1000) {
        try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
        await within(exited, 3000, 'Unready supervisor failed to stop');
        throw new Error('Supervisor failed to become ready for verified cleanup');
      }
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    if (!closed) child.kill('SIGTERM');
    let exit;
    try { exit = await within(exited, 3000, 'Subreaper failed to stop within deadline'); }
    catch (error) {
      try { process.kill(-child.pid, 'SIGKILL'); } catch (killError) { if (killError.code !== 'ESRCH') throw killError; }
      throw error;
    }
    const evidence = await cleanupEvidence();
    return { ...evidence, exit, elapsedMs: performance.now() - started };
  }
  async function cleanupEvidence() {
    if (!cleanup) {
      cleanup = JSON.parse(await readFile(cleanupPath, 'utf8'));
      await rm(temporary, { recursive: true, force: true });
    }
    if (!cleanup.allExited || !cleanup.allReaped) throw new Error(`Incomplete owned-process cleanup: ${JSON.stringify(cleanup)}`);
    return cleanup;
  }
  async function bounded(command, args, deadlineMs = 30000, signal) {
    if (active) throw new Error('Only one operation may run per controller');
    if (!(deadlineMs > 0 && Number.isFinite(deadlineMs))) throw new Error('Invalid deadline');
    active = true;
    let timer;
    let abort;
    try {
      return await new Promise((resolve, reject) => {
        let settled = false;
        let stopping = false;
        const stop = async (outcome) => {
          if (settled || stopping) return;
          stopping = true;
          clearTimeout(timer);
          try { resolve({ outcome, termination: await terminate() }); }
          catch (error) { reject(error); }
        };
        abort = () => { void stop('cancelled'); };
        if (signal?.aborted) return abort();
        signal?.addEventListener('abort', abort, { once: true });
        request(command, args).then(
          (result) => { if (!stopping) { settled = true; resolve(result); } },
          (error) => { if (!stopping) { settled = true; reject(error); } },
        );
        timer = setTimeout(() => { void stop('timeout'); }, deadlineMs);
      });
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); active = false; }
  }
  const controller = {
    supervisorPid: child.pid,
    discover: (deadlineMs) => bounded('discover', {}, deadlineMs),
    run: ({ testIds, environment, request: jobRequest, deadlineMs, signal } = {}) => bounded('run', { testIds, environment, request: jobRequest }, deadlineMs, signal),
    diagnostics: () => ({ output, truncated }),
    terminate,
    async close() {
      try {
        if (!closed) {
          await bounded('close', {}, 10000);
          if (child.connected) child.disconnect();
          await within(exited, 3000, 'Controller did not close');
        }
      } catch (error) {
        await terminate();
        throw error;
      }
      return cleanupEvidence();
    },
  };
  try {
    const initializationBudget = options.initializationDeadlineMs === undefined ? 30000 : Math.max(1, options.initializationDeadlineMs - (performance.now() - openingStarted));
    const initialized = await bounded('initialize', options, initializationBudget);
    if (initialized.outcome === 'timeout') {
      const error = new Error('Controller initialization timed out after verified cleanup');
      error.code = 'OXIMUT_TIMEOUT';
      throw error;
    }
    controller.versions = initialized.versions;
    controller.pid = initialized.controllerPid;
    controller.reuse = initialized.reuse;
    return controller;
  } catch (error) {
    if (!closed) await terminate();
    const wrapped = new Error(`${error.message}\n${output}`, { cause: error });
    wrapped.code = error.code;
    throw wrapped;
  }
}

/** Compare identities and result payloads, excluding performance/PID observations. */
export function comparable(run) {
  return {
    outcome: run.outcome,
    errors: run.errors,
    tests: run.tests.map(({ id, project, file, name, state, errors }) => ({ id, project, file, name, state, errors })).sort((a, b) => a.id.localeCompare(b.id)),
  };
}
