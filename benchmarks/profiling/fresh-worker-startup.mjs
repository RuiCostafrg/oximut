import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const [captureArg, outputArg, roundsArg = '12'] = process.argv.slice(2);
const capture = resolve(captureArg);
const output = resolve(outputArg);
const rounds = Number(roundsArg);
assert(Number.isSafeInteger(rounds) && rounds > 0);
assert(!process.env.NODE_OPTIONS && !process.env.NODE_DISABLE_COMPILE_CACHE);
assert.equal(process.versions.node, '26.3.1');
const width = 4;
const sources = (await readdir(capture)).filter((name) => /^startup-contract-\d+\.json$/.test(name)).sort();
assert(sources.length > 0, 'missing runtime launch contract');
const contracts = await Promise.all(sources.map(async (name) => JSON.parse(await readFile(resolve(capture, name)))));
const launch = contracts[0];
const snapshotFlag = launch.execArgv.find((arg) => arg.startsWith('--snapshot-blob='));
const launchKeys = ['entry', 'argv', 'execArgv', 'stdio', 'serialization'];
for (const contract of contracts) {
  for (const key of launchKeys) assert.deepEqual(contract[key], launch[key], key);
}
assert.equal(launch.serialization, 'advanced');
assert.equal(launch.stdio, 'pipe');
assert.match(launch.entry, /\/vitest\/dist\/workers\/forks\.js$/);
assert(launch.execArgv.some((arg) => arg.endsWith('/compile-cache.cjs')));
const probe = resolve(import.meta.dirname, 'fresh-worker-probe.cjs');
const cacheRoot = await mkdtemp(resolve(output, 'cache-'));
const cells = snapshotFlag
  ? [['vitest', 'populated'], ['snapshot', 'populated'], ['snapshot', 'disabled'],
    ['snapshot-small-pool', 'populated'], ['snapshot-small-pool', 'disabled']]
  : ['cold', 'populated', 'disabled'].flatMap(mode => [['minimal', mode], ['vitest', mode]]);
const cacheState = async (dir) => {
  const files = (await readdir(dir, {recursive: true, withFileTypes: true})).filter((entry) => entry.isFile());
  const sizes = await Promise.all(files.map((entry) => stat(resolve(entry.parentPath, entry.name))));
  return {files: files.length, bytes: sizes.reduce((bytes, entry) => bytes + entry.size, 0)};
};

async function sample(kind, mode, round, lane, cacheDir, phase, beforeCache) {
  const usesSnapshot = kind.startsWith('snapshot');
  const entry = kind === 'minimal' ? resolve(import.meta.dirname, 'minimal-ipc-worker.mjs') : launch.entry;
  const env = {...process.env, ...launch.env, NODE_OPTIONS: '--require=' + JSON.stringify(probe),
    NODE_COMPILE_CACHE: cacheDir};
  if (mode === 'disabled') env.NODE_DISABLE_COMPILE_CACHE = '1';
  const execArgv = launch.execArgv.filter(arg => arg !== snapshotFlag);
  if (kind === 'snapshot-small-pool') execArgv.push('--v8-pool-size=1');
  if (usesSnapshot) {
    execArgv.push(snapshotFlag);
    env.OXIMUT_SNAPSHOT_BLOB = snapshotFlag.slice('--snapshot-blob='.length);
  }

  const time = () => String(process.hrtime.bigint());
  const forkNs = time();
  const child = fork(entry, launch.argv, {
    env, execArgv, cwd: launch.cwd, stdio: launch.stdio, serialization: launch.serialization,
  });
  let stderr = '';
  child.stdout.resume();
  child.stderr.setEncoding('utf8').on('data', (chunk) => { stderr += chunk; });
  const exit = once(child, 'exit');
  exit.catch(() => {});
  const send = async (type, message, expected) => {
    const ns = time();
    const receive = Promise.race([once(child, 'message'), exit.then(([code, signal]) => {
      throw Error(`pid ${child.pid} exited before ${expected}: ${code}/${signal}: ${stderr}`);
    })]);
    child.send({__vitest_worker_request__: true, type, ...message});
    const [response] = await receive;
    const receiptNs = time();
    assert.equal(response.type, expected);
    assert.equal(response.__vitest_worker_response__, true);
    assert.equal(response.error, undefined);
    assert(response.diagnostic?.beforeStart);
    return {sendNs: ns, receiptNs, ...response.diagnostic};
  };
  const timeout = setTimeout(() => child.kill('SIGKILL'), 15_000);
  try {
    const start = await send('start', launch.start, 'started');
    const stop = await send('stop', {}, 'stopped');
    assert(child.kill('SIGTERM'));
    const [code, signal] = await exit;
    const exitNs = time();
    assert.deepEqual([code, signal], [null, 'SIGTERM']);
    return {kind, mode, round, lane, phase, pid: child.pid, beforeCache,
      forkNs, start, stop, exitNs, code, signal, stderr, ...(snapshotFlag ? {execArgv} : {})};
  } finally {
    clearTimeout(timeout);
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await exit;
  }
}

const records = [];
async function wave(kind, mode, round, phase) {
  const dirs = await Promise.all(Array.from({length: width}, async (_, lane) => {
    if (mode === 'populated') return resolve(cacheRoot, `${kind}-primed-${lane}`);
    return mkdtemp(resolve(cacheRoot, 'slot-'));
  }));
  await Promise.all(dirs.map((dir) => mkdir(dir, {recursive: true})));
  const before = await Promise.all(dirs.map(cacheState));
  const shouldBePopulated = mode === 'populated' && phase === 'measure';
  assert(before.every((state) => (state.files > 0) === shouldBePopulated));
  const results = await Promise.allSettled(dirs.map((dir, lane) => sample(kind, mode, round, lane, dir, phase, before[lane])));
  for (const result of results) if (result.status === 'rejected') throw result.reason;
  const after = await Promise.all(dirs.map(cacheState));
  for (const [lane, result] of results.entries()) {
    const record = {...result.value, afterCache: after[lane]};
    records.push(record);
    assert.equal(record.afterCache.files > 0, mode !== 'disabled');
  }
  if (mode !== 'populated') await Promise.all(dirs.map((dir) => rm(dir, {recursive: true})));
}

try {
  for (const [kind, mode] of cells) {
    if (mode === 'populated') await wave(kind, mode, -1, 'prime');
  }
  for (let round = 0; round < rounds; round++) {
    // Rotate cell order, keeping real contenders in groups of four fresh forks.
    for (let offset = 0; offset < cells.length; offset++) {
      const [kind, mode] = cells[(round + offset) % cells.length];
      await wave(kind, mode, round, 'measure');
    }
  }
} finally {
  await rm(cacheRoot, {recursive: true, force: true});
  await writeFile(resolve(output, snapshotFlag ? 'snapshot-launch-replay.json' : 'startup-replay.json'), JSON.stringify({
    node: process.version, v8: process.versions.v8, arch: process.arch, execPath: process.execPath,
    nodeOptions: '--require=' + JSON.stringify(probe), contracts: sources, width, rounds, records,
  }, null, 2) + '\n', {flag: 'wx'});
}
