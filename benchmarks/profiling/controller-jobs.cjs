// Diagnostic preload only. No signals, timers, deadline changes or user payloads.
const dir = process.env.OXIMUT_PROFILE_CONTROLLER_DIR;
const workerPhases = process.env.OXIMUT_PROFILE_WORKER_PHASES === '1';
if (dir && workerPhases && process.env.OXIMUT_BUDGETED === '1'
    && process.argv[1]?.endsWith('/vitest/dist/workers/forks.js')
    && typeof process.send === 'function') {
  const originalSend = process.send;
  const cpuUsage = process.cpuUsage.bind(process);
  const checkpoints = {};
  const pid = process.pid;
  const ppid = process.ppid;
  process.send = function (...args) {
    // Vitest can terminate the worker on receipt of 'stopped', so persist
    // these counters before sending that final acknowledgement.
    try {
      const msg = args[0];
      if (msg?.m === 'onCollected') {
        checkpoints.firstCollection ??= cpuUsage();
      } else if (msg?.__vitest_worker_response__ === true) {
        if (msg.type === 'started') checkpoints.started ??= cpuUsage();
        if (msg.type === 'testfileFinished') checkpoints.finished ??= cpuUsage();
        if (msg.type === 'stopped' && !checkpoints.stopped) {
          checkpoints.stopped = cpuUsage();
          const fs = require('node:fs');
          const path = require('node:path');
          fs.writeFileSync(path.join(dir, `worker-${ppid}-${pid}.json`),
            JSON.stringify({pid, ppid, checkpoints}), {flag: 'wx'});
        }
      }
    } catch { /* The missing/malformed artifact fails capture validation. */ }
    return Reflect.apply(originalSend, this, args);
  };
}

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
  const startupContract = process.env.OXIMUT_PROFILE_STARTUP_CONTRACT;
  const validSelector = (id) => typeof id === 'string' && /^[a-f0-9]{64}$/.test(id) ? id : null;
  let startupContractWritten = false;
  cp.fork = function (...args) {
    const selector = args[2]?.env?.OXIMUT_ACTIVE_MUTANT;
    const child = Reflect.apply(originalFork, this, args);
    observe(() => {
      emit('fork', { childPid: child.pid,
        ...(startupContract ? {selector: validSelector(selector)} : {}) });
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
          if (msg?.__vitest_worker_request__ === true && controls.has(msg.type)) {
            const files = startupContract && msg.type === 'run' && selector
              ? msg.context.files.map((file) => ({
                filepath: file.filepath,
                fields: Object.keys(file).sort(),
              })) : null;
            emit('child-send', { childPid: child.pid, messageType: msg.type, ...(files ? {files} : {}) });
            if (startupContract && process.env.OXIMUT_PROFILE_STARTUP_REPLAY !== '0'
                && !startupContractWritten && msg.type === 'start'
                && args[0].endsWith('/vitest/dist/workers/forks.js')
                && args[2]?.env?.OXIMUT_ACTIVE_MUTANT) {
              // Keep the fields consumed before "started"; avoid exporting
              // the serialized project's environment or the inherited process env.
              const {config, environment, pool} = msg.context;
              const {root, name, environmentOptions, experimental, chaiConfig,
                disableColors, serializedDefines, env: projectEnv} = config;
              const fixtureEnv = ['BASE_URL', 'MODE', 'DEV', 'PROD', 'SSR', 'OXIMUT_ACTIVE_MUTANT'];
              if (serializedDefines || environment.name !== 'node' || experimental.viteModuleRunner === false
                  || msg.traces.enabled || msg.traces.sdkPath
                  || Object.keys(projectEnv).some((key) => !fixtureEnv.includes(key)))
                throw Error('Unsupported startup contract for isolated replay');
              const {env, execArgv, stdio, serialization} = args[2];
              const record = {
                pid: child.pid, cwd: process.cwd(), entry: args[0], argv: args[1],
                execArgv, stdio, serialization,
                env: Object.fromEntries(['CI', 'NO_COLOR', 'TEST', 'VITEST', 'NODE_ENV',
                  'VITEST_MODE', 'FORCE_TTY', 'OXIMUT_BUDGETED', 'OXIMUT_ACTIVE_MUTANT']
                  .filter((key) => env[key] !== undefined).map((key) => [key, env[key]])),
                start: {
                  ...msg, context: {environment, pool, config: {
                    root, name, environmentOptions, experimental, chaiConfig, disableColors, env: projectEnv,
                  }},
                },
              };
              fs.writeFileSync(startupContract + `-${process.pid}.json`, JSON.stringify(record), {flag: 'wx'});
              startupContractWritten = true;
            }
          }
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
    const command = msg.command === 'run-shared-lane-resident-v1' ? 'run' : msg.command;
    const job = command === 'run' ? msg.args?.request : undefined;
    const kind = job?.kind;
    const mutantId = kind === 'mutant' && typeof job.mutation?.id === 'string'
      && /^[a-f0-9]{64}$/.test(job.mutation.id) ? job.mutation.id : null;
    const jobId = typeof job?.jobId === 'string' && job.jobId.length <= 128
      && /^(?:baseline|instrumented-baseline(?:-[0-9]+)?|[a-f0-9]{64})$/.test(job.jobId) ? job.jobId : null;
    const runId = typeof job?.runId === 'string' && job.runId.length <= 128
      && /^[0-9]+-[0-9]+$/.test(job.runId) ? job.runId : null;
    // A shared-lane batch is its first lane's request carrying every lane's request.
    const lanes = command === 'run' && Array.isArray(job?.lanes) ? job.lanes : undefined;
    const laneMutantIds = lanes && lanes.length <= 1024 && lanes.every((lane) => lane?.kind === 'mutant'
      && typeof lane.mutation?.id === 'string' && /^[a-f0-9]{64}$/.test(lane.mutation.id))
      ? lanes.map((lane) => lane.mutation.id) : null;
    const laneJobIds = laneMutantIds && lanes.every((lane) => typeof lane.jobId === 'string'
      && /^[a-f0-9]{64}$/.test(lane.jobId)) ? lanes.map((lane) => lane.jobId) : null;
    const laneRunIds = laneMutantIds && lanes.every((lane) => typeof lane.runId === 'string'
      && lane.runId.length <= 128 && /^[0-9]+-[0-9]+$/.test(lane.runId)) ? lanes.map((lane) => lane.runId) : null;
    emit('controller-request', { id: msg.id, command, wireCommand: msg.command,
      kind: ['baseline', 'mutant'].includes(kind) ? kind : null, mutantId, jobId, runId,
      ...(lanes ? { laneCount: lanes.length, laneMutantIds, laneJobIds, laneRunIds } : {}) });
    if (!workerPhases && command === 'run' && kind === 'mutant' && !attempted) startProfile(msg.id);
  }));
  process.on('exit', (code) => emit('controller-exit', { code }));
}
