// Diagnostic preload only. No signals, timers, deadline changes or user payloads.
const dir = process.env.OXIMUT_PROFILE_CONTROLLER_DIR;
if (dir && process.argv[1]?.endsWith('/m2-worker.ts')) {
  const fs = require('node:fs');
  const path = require('node:path');
  const inspector = require('node:inspector');
  const cp = require('node:child_process');
  const { syncBuiltinESMExports } = require('node:module');
  const origin = process.hrtime.bigint();
  const timeline = path.join(dir, `controller-${process.pid}.jsonl`);
  const appendError = path.join(dir, `controller-${process.pid}.timeline-error`);
  let sequence = 0;
  let timelineWriteFailed = false;
  const guard = (fn) => { try { return fn(); } catch { /* Missing evidence fails validation. */ } };
  const emit = (type, meta = {}) => {
    const seq = ++sequence;
    try {
      fs.appendFileSync(timeline, JSON.stringify({
        type, seq, timelineWriteFailed, monotonicMs: Number(process.hrtime.bigint() - origin) / 1e6,
        ...(['controller-start', 'controller-request', 'controller-send', 'controller-exit'].includes(type)
          ? { controllerMemoryBytes: process.memoryUsage() } : {}),
        selfCpuUsage: process.cpuUsage(), ...meta,
      }) + '\n');
    } catch {
      timelineWriteFailed = true;
      guard(() => fs.writeFileSync(appendError, 'Timeline append failed; capture incomplete\n'));
    }
  };
  const reportError = (error) => emit('profile-error', { error: String(error).slice(0, 500) });
  const observe = (fn) => { try { fn(); } catch (error) { reportError(error); } };
  let attempted = false;
  let profileJob;
  let session;
  const disconnect = () => {
    profileJob = undefined;
    guard(() => session?.disconnect());
  };
  const post = (method, cb) => {
    let inPost = true;
    let responded = false;
    try {
      session.post(method, (error, result) => {
        if (!inPost) {
          reportError(`${method} callback did not complete synchronously; capture incomplete`);
          disconnect();
          return;
        }
        responded = true;
        if (error) { reportError(error); disconnect(); return; }
        observe(() => cb(result));
      });
      if (!responded) {
        reportError(`${method} did not complete synchronously; capture incomplete`);
        disconnect();
      }
    } catch (error) { reportError(error); disconnect(); }
    finally { inPost = false; }
  };
  const startProfile = (jobId) => {
    attempted = true;
    session = new inspector.Session();
    session.connect();
    // Local inspector commands normally complete inside post(). Never defer
    // application dispatch: if that changes, reject this capture instead.
    post('Profiler.enable', () => post('Profiler.start', () => {
      profileJob = jobId;
      emit('profile-started', { jobId });
    }));
  };
  const stopProfile = (jobId) => {
    profileJob = undefined;
    post('Profiler.stop', ({ profile }) => {
      try {
        const name = `controller-mutant-${process.pid}-${jobId}.cpuprofile`;
        fs.writeFileSync(path.join(dir, `${name}.tmp`), JSON.stringify(profile));
        fs.renameSync(path.join(dir, `${name}.tmp`), path.join(dir, name));
        emit('profile-written', { jobId, profile: name });
      } finally { disconnect(); }
    });
  };
  observe(() => {
    const startTime = fs.readFileSync('/proc/self/stat', 'utf8').split(') ').pop().split(' ')[19];
    if (!/^\d+$/.test(startTime)) throw Error('Invalid process identity');
    emit('controller-start', { formatVersion: 1, pid: process.pid, ppid: process.ppid, startTime,
      entrypoint: process.argv[1], cwd: process.cwd(), monotonicOriginNs: String(origin) });
  });

  const controls = new Set(['start', 'run', 'collect', 'stop']);
  const responses = new Set(['started', 'testfileFinished', 'stopped']);
  const methods = new Set(['fetch', 'fetchModule', 'resolveId', 'onCollected', 'onTaskUpdate',
    'onQueued', 'onFinished', 'onAfterSuiteRun', 'onUserConsoleLog']);
  const originalFork = cp.fork;
  cp.fork = function (...args) {
    const child = Reflect.apply(originalFork, this, args);
    observe(() => {
      emit('fork', { childPid: child.pid });
      const counts = Object.create(null);
      child.on('message', (msg) => observe(() => {
        if (!msg || typeof msg !== 'object') return;
        const messageType = msg.__vitest_worker_response__ === true && responses.has(msg.type) ? msg.type : null;
        const rpcMethod = methods.has(msg.m) ? msg.m : null;
        const key = messageType || rpcMethod;
        if (!key) return;
        counts[key] = (counts[key] || 0) + 1;
        if (counts[key] === 1) emit('child-message', { childPid: child.pid, messageType, rpcMethod });
      }));
      const send = child.send;
      child.send = function (...messages) {
        observe(() => {
          const msg = messages[0];
          if (msg?.__vitest_worker_request__ === true && controls.has(msg.type))
            emit('child-send', { childPid: child.pid, messageType: msg.type });
        });
        return Reflect.apply(send, this, messages);
      };
      child.on('exit', (code, signal) => emit('child-exit', { childPid: child.pid, code, signal, counts }));
    });
    return child;
  };
  syncBuiltinESMExports();
  const originalSend = process.send;
  if (typeof originalSend === 'function') process.send = function (...args) {
    const msg = args[0];
    // A normal return observes a local send invocation, not delivery or callback completion.
    // Preserve exceptions, return value, receiver, arguments and callback semantics.
    const result = Reflect.apply(originalSend, this, args);
    observe(() => {
      if (msg?.protocol === 1 && Number.isSafeInteger(msg.id))
        emit('controller-send', { id: msg.id, boundary: 'send-return', sendReturn: result,
          result: Object.hasOwn(msg, 'result'), error: Object.hasOwn(msg, 'error') });
    });
    observe(() => { if (profileJob !== undefined && msg?.id === profileJob) stopProfile(profileJob); });
    return result;
  };
  process.prependListener('message', (msg) => observe(() => {
    if (msg?.protocol !== 1 || !Number.isSafeInteger(msg.id)) return;
    // initialize takes direct arguments; run wraps the Request in args.request.
    const job = msg.command === 'run' ? msg.args?.request : undefined;
    const kind = job?.kind;
    const mutantId = kind === 'mutant' && typeof job.mutation?.id === 'string'
      && /^[a-f0-9]{64}$/.test(job.mutation.id) ? job.mutation.id : null;
    const jobId = typeof job?.jobId === 'string' && job.jobId.length <= 128
      && /^(?:baseline|instrumented-baseline(?:-[0-9]+)?|[a-f0-9]{64})$/.test(job.jobId) ? job.jobId : null;
    const runId = typeof job?.runId === 'string' && job.runId.length <= 128
      && /^[0-9]+-[0-9]+$/.test(job.runId) ? job.runId : null;
    // A shared-lane batch is its first lane's request carrying every lane's request.
    const lanes = msg.command === 'run' && Array.isArray(job?.lanes) ? job.lanes : undefined;
    const laneMutantIds = lanes && lanes.length <= 1024 && lanes.every((lane) => lane?.kind === 'mutant'
      && typeof lane.mutation?.id === 'string' && /^[a-f0-9]{64}$/.test(lane.mutation.id))
      ? lanes.map((lane) => lane.mutation.id) : null;
    emit('controller-request', { id: msg.id, command: msg.command,
      kind: ['baseline', 'mutant'].includes(kind) ? kind : null, mutantId, jobId, runId,
      ...(lanes ? { laneCount: lanes.length, laneMutantIds } : {}) });
    if (msg.command === 'run' && kind === 'mutant' && !attempted) startProfile(msg.id);
  }));
  process.on('exit', (code) => emit('controller-exit', { code }));
}
