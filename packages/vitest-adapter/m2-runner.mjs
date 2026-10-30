import { appendFile, lstat, open, readFile, readdir, rename, rm, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { hasUnexpectedDescendants, openController } from './index.mjs';
import { envelope, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, validateRequest } from './protocol.mjs';

const [session] = process.argv.slice(2);
if (!session || process.argv.length !== 3) throw new Error('Usage: m2-runner.mjs SESSION_DIR');
const workerFile = fileURLToPath(new URL('./m2-worker.ts', import.meta.url));
const sleep = () => new Promise((resolve) => setTimeout(resolve, 15));
let controller;
let fingerprint;
let jobs = 0;
let descendants = new Map();
const empty = (outcome, error) => ({
  outcome,
  tests: { passed: 0, failed: 0, skipped: 0 },
  errors: [error],
});
async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}
/** Parent PID and start time; a PID alone can name a later process generation. */
async function identity(pid) {
  const value = await readFile(`/proc/${pid}/stat`, 'utf8');
  const fields = value.slice(value.lastIndexOf(')') + 2).split(' ');
  if (fields.length < 20 || !/^\d+$/.test(fields[1]) || !/^\d+$/.test(fields[19]))
    throw new Error(`Malformed /proc/${pid}/stat`);
  return { parent: Number(fields[1]), start: fields[19] };
}
async function tree(pid, checkpoint = () => {}) {
  const parents = new Map();
  for (const entry of await readdir('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    checkpoint();
    try {
      parents.set(Number(entry), await identity(entry));
    } catch (error) {
      if (!['ENOENT', 'ESRCH'].includes(error.code)) throw error;
    }
  }
  checkpoint();
  const result = new Map();
  let added = true;
  while (added) {
    added = false;
    for (const [child, { parent, start }] of parents) {
      if (child !== pid && !result.has(child) && (parent === pid || result.has(parent))) {
        result.set(child, start);
        added = true;
      }
    }
  }
  return result;
}
async function inputs(root, config) {
  const entries = [];
  let symlink = false;
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (['node_modules', '.git', '.oximut'].includes(entry.name)) continue;
      const path = join(directory, entry.name);
      if (resolve(path) === resolve(session)) continue;
      if (entry.isSymbolicLink()) {
        symlink = true;
        const link = await lstat(path);
        entries.push([path, link.size, link.mtimeMs, link.ctimeMs]);
      } else if (entry.isDirectory()) await visit(path);
      else {
        const item = await stat(path);
        entries.push([path, item.size, item.mtimeMs, item.ctimeMs]);
      }
    }
  }
  await visit(root);
  const configInfo = await stat(config);
  entries.push([config, configInfo.size, configInfo.mtimeMs, configInfo.ctimeMs]);
  return { entries: entries.sort(([a], [b]) => a.localeCompare(b)), symlink };
}
async function close() {
  if (controller) {
    const owned = controller;
    controller = undefined;
    await owned.close();
  }
}
async function run(request) {
  const started = performance.now();
  const remaining = () => request.deadlineMs - (performance.now() - started);
  // Synchronous /proc reads cannot be interrupted; check between them instead.
  const checkpoint = () => {
    if (remaining() > 0) return;
    const error = new Error('Job deadline exceeded during descendant verification');
    error.code = 'OXIMUT_TIMEOUT';
    throw error;
  };
  const snapshot = await inputs(request.root, request.config);
  const next = JSON.stringify([request.root, request.config, snapshot.entries]);
  if (snapshot.symlink || fingerprint !== next || jobs >= 50) await close();
  fingerprint = next;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      if (remaining() <= 0) return empty('timeout', 'Job deadline exceeded');
      if (!controller) {
        controller = await openController({
          ...request,
          workerFile,
          initializationDeadlineMs: remaining(),
        });
        if (snapshot.symlink) controller.reuse = 'fresh: symlink inputs require fresh evaluation';
        await appendFile(
          join(session, 'events.jsonl'),
          `${JSON.stringify({
            jobId: request.jobId,
            controllerPid: controller.pid,
            reuse: controller.reuse,
          })}\n`,
        );
        jobs = 0;
        descendants = await tree(controller.supervisorPid, checkpoint);
      }
      const result =
        remaining() <= 0
          ? { outcome: 'timeout', termination: await controller.terminate() }
          : await controller.run({ request, deadlineMs: remaining() });
      jobs++;
      const retire =
        result.outcome === 'timeout' ||
        result.outcome === 'infrastructure-error' ||
        controller.reuse !== 'controller';
      // A baseline PID reused by a test process is a new, unexpected generation.
      const unexpectedDescendants = async () =>
        [...(await tree(controller.supervisorPid, checkpoint))].filter(
          ([pid, start]) => descendants.get(pid) !== start,
        );
      // A completed test fork can exit while /proc is being enumerated. Confirm
      // disappearance independently; an empty second tree can miss reparenting.
      const leaked =
        !retire &&
        (await hasUnexpectedDescendants(
          unexpectedDescendants,
          async ([pid, start]) => (await identity(pid)).start === start,
          checkpoint,
        ));
      // Verification may cross the deadline; never publish a late result. Only
      // verification is bounded here: retiring a controller is not job time.
      if (!retire) checkpoint();
      // The supervisor reaps even detached descendants before another job starts.
      if (retire || leaked) await close();
      if (result.termination)
        return empty('timeout', 'Job deadline exceeded; owned processes reaped');
      const crash =
        result.outcome === 'infrastructure-error' &&
        result.errors?.some((error) =>
          /(?:Worker exited|unexpectedly exited|process\.exit unexpectedly called|Channel closed|IPC channel)/i.test(
            error,
          ),
        );
      if (crash && process.env.OXIMUT_BUDGETED !== '1' && attempt === 0 && remaining() > 0)
        continue;
      return result;
    } catch (error) {
      await close();
      if (error.code === 'OXIMUT_TIMEOUT') return empty('timeout', error.message);
      const crash = /(?:Controller exited|Channel closed|IPC channel)/i.test(error.message);
      if (crash && process.env.OXIMUT_BUDGETED !== '1' && attempt === 0 && remaining() > 0)
        continue;
      return empty('infrastructure-error', error.message);
    }
  }
}
try {
  // Requests are strictly serial. Reuse the bounded buffer instead of allocating
  // another 8 MiB external allocation for every mutation job. Only bytesRead
  // participates in parsing, so shorter requests cannot observe an old suffix.
  const requestBuffer = Buffer.alloc(MAX_REQUEST_BYTES + 1);
  for (let sequence = 0; ; sequence++) {
    const requestPath = join(session, `request-${sequence}.json`);
    while (!(await exists(requestPath))) {
      if (await exists(join(session, 'stop'))) {
        await close();
        process.exit(0);
      }
      await sleep();
    }
    let request;
    let result;
    try {
      const file = await open(requestPath, 'r');
      try {
        const info = await file.stat();
        if (!info.isFile() || info.size > MAX_REQUEST_BYTES)
          throw new Error('Request must be a regular file of at most 8 MiB');
        const { bytesRead } = await file.read(requestBuffer, 0, requestBuffer.length, 0);
        if (bytesRead > MAX_REQUEST_BYTES) throw new Error('Request exceeds 8 MiB');
        request = JSON.parse(requestBuffer.toString('utf8', 0, bytesRead));
      } finally {
        await file.close();
      }
      validateRequest(request);
      result = await run(request);
    } catch (error) {
      result = empty('infrastructure-error', error.message);
    }
    const destination = join(session, `response-${sequence}.json`);
    const temporary = `${destination}.${process.pid}.tmp`;
    try {
      const payload = JSON.stringify(envelope(request, result));
      if (Buffer.byteLength(payload) > MAX_RESPONSE_BYTES)
        throw new Error('Response exceeds 64 KiB');
      const file = await open(temporary, 'wx', 0o600);
      try {
        await file.writeFile(payload);
      } finally {
        await file.close();
      }
      await rename(temporary, destination);
    } finally {
      await rm(temporary, { force: true });
    }
  }
} finally {
  await close();
}
