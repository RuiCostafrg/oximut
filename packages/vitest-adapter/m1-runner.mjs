import { open, rename, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { openController } from './index.mjs';
import { envelope, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, validateRequest } from './protocol.ts';

const started = performance.now();
const [requestPath, responsePath] = process.argv.slice(2);
let request;
let controller;
let result = {
  outcome: 'infrastructure-error',
  tests: { passed: 0, failed: 0, skipped: 0 },
  errors: [],
};
try {
  if (!requestPath || !responsePath || process.argv.length !== 4 || requestPath === responsePath)
    throw new Error('Usage: m1-runner.mjs REQUEST_PATH RESPONSE_PATH (distinct files)');
  const file = await open(requestPath, 'r');
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > MAX_REQUEST_BYTES)
      throw new Error('Request must be a regular file of at most 8 MiB');
    const buffer = Buffer.alloc(MAX_REQUEST_BYTES + 1);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > MAX_REQUEST_BYTES) throw new Error('Request exceeds 8 MiB');
    request = JSON.parse(buffer.toString('utf8', 0, bytesRead));
  } finally {
    await file.close();
  }
  validateRequest(request);
  const remaining = () => Math.max(1, request.deadlineMs - (performance.now() - started));
  controller = await openController({
    ...request,
    workerFile: fileURLToPath(new URL('./m1-worker.ts', import.meta.url)),
    initializationDeadlineMs: remaining(),
  });
  result =
    performance.now() - started >= request.deadlineMs
      ? { outcome: 'timeout', termination: await controller.terminate() }
      : await controller.run({ deadlineMs: remaining() });
  if (result.outcome === 'timeout' && result.termination)
    result = {
      outcome: 'timeout',
      tests: { passed: 0, failed: 0, skipped: 0 },
      errors: ['Job deadline exceeded; owned processes reaped'],
    };
} catch (error) {
  result = {
    outcome: error.code === 'OXIMUT_TIMEOUT' ? 'timeout' : 'infrastructure-error',
    tests: { passed: 0, failed: 0, skipped: 0 },
    errors: [error.message],
  };
} finally {
  if (controller) {
    try {
      await controller.close();
    } catch (error) {
      result = {
        outcome: 'infrastructure-error',
        tests: { passed: 0, failed: 0, skipped: 0 },
        errors: [`Cleanup failed: ${error.message}`],
      };
    }
  }
}
if (!responsePath) {
  process.stderr.write(`${result.errors.join('\n')}\n`);
  process.exitCode = 1;
} else {
  const temporary = `${responsePath}.${process.pid}.tmp`;
  try {
    const payload = JSON.stringify(envelope(request, result));
    if (Buffer.byteLength(payload) > MAX_RESPONSE_BYTES) throw new Error('Response exceeds 64 KiB');
    const file = await open(temporary, 'wx', 0o600);
    try {
      await file.writeFile(payload);
    } finally {
      await file.close();
    }
    await rename(temporary, responsePath);
  } catch (error) {
    await rm(temporary, { force: true });
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}
