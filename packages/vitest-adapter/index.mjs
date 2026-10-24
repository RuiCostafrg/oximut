import { fork } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const controllerFile = fileURLToPath(new URL('./worker.mjs', import.meta.url));
const supervisorFile = fileURLToPath(new URL('./supervisor.py', import.meta.url));

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
  if (process.platform !== 'linux') throw new Error('M0 deadline/process-tree verification requires Linux');
  const temporary = await mkdtemp(join(tmpdir(), 'oximut-controller-'));
  const cleanupPath = join(temporary, 'cleanup.json');
  const child = fork(controllerFile, [cleanupPath], {
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
    run: ({ testIds, environment, deadlineMs, signal } = {}) => bounded('run', { testIds, environment }, deadlineMs, signal),
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
    const initialized = await bounded('initialize', options, 30000);
    if (initialized.outcome === 'timeout') throw new Error('Controller initialization timed out');
    controller.versions = initialized.versions;
    controller.pid = initialized.controllerPid;
    return controller;
  } catch (error) {
    if (!closed) await terminate();
    throw new Error(`${error.message}\n${output}`, { cause: error });
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
