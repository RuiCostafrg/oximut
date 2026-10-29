import { appendFile, lstat, open, readFile, readdir, rename, rm, stat } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { openController } from './index.mjs';
import { envelope, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, validateRequest } from './protocol.ts';

const [session] = process.argv.slice(2);
if (!session || process.argv.length !== 3) throw new Error('Usage: m2-runner.mjs SESSION_DIR');
const workerFile = fileURLToPath(new URL('./m2-worker.ts', import.meta.url));
const sleep = () => new Promise((resolve) => setTimeout(resolve, 15));
let controller;
let fingerprint;
let jobs = 0;
let descendants = new Set();
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
async function tree(pid) {
  const parents = new Map();
  for (const entry of await readdir('/proc')) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const value = await readFile(`/proc/${entry}/stat`, 'utf8');
      const fields = value.slice(value.lastIndexOf(')') + 2).split(' ');
      parents.set(Number(entry), Number(fields[1]));
    } catch (error) {
      if (!['ENOENT', 'ESRCH'].includes(error.code)) throw error;
    }
  }
  const result = new Set();
  let added = true;
  while (added) {
    added = false;
    for (const [child, parent] of parents) {
      if (child !== pid && !result.has(child) && (parent === pid || result.has(parent))) {
        result.add(child);
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
        descendants = await tree(controller.supervisorPid);
      }
      const result =
        remaining() <= 0
          ? { outcome: 'timeout', termination: await controller.terminate() }
          : await controller.run({ request, deadlineMs: remaining() });
      jobs++;
      const leaked = [...(await tree(controller.supervisorPid))].some(
        (pid) => !descendants.has(pid),
      );
      // The supervisor reaps even detached descendants before another job starts.
      if (
        result.outcome === 'timeout' ||
        result.outcome === 'infrastructure-error' ||
        controller.reuse !== 'controller' ||
        leaked
      )
        await close();
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
