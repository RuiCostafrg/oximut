// TypeScript consumers share the checked JavaScript implementation.
export {
  envelope,
  MAX_REQUEST_BYTES,
  MAX_RESPONSE_BYTES,
  restoreLaneResidents,
  SHARED_LANE_RESIDENT,
  validateRequest,
} from './protocol.mjs';

export interface Selection {
  mode: 'files' | 'full' | 'none';
  files: string[];
  selectedTests: number;
  totalTests: number;
  expectedSkipped?: number;
  reasons: string[];
}
export interface Coverage {
  complete: boolean;
  tests: { id: string; file: string; name: string }[];
  hits: {
    mutantId: string;
    tests: string[];
    outsideTest: boolean;
    /** Compact form of `tests` for large suites: inclusive index runs into `Coverage.tests`. */
    testRanges?: [number, number][];
  }[];
  reasons: string[];
}

export interface Request {
  lanes?: Omit<Request, 'lanes'>[];
  protocol: 1;
  runId: string;
  jobId: string;
  generation: number;
  kind: 'baseline' | 'mutant';
  root: string;
  config: string;
  deadlineMs: number;
  /** Per-baseline file concurrency; mutants always use one worker. */
  workers?: number;
  mutation: null | { id: string; file: string; code: string; map: Record<string, unknown> };
  pool?: 'threads';
  collectCoverage?: NonNullable<Request['mutation']>[];
  /** Shared switched modules served for every listed file throughout the job. */
  resident?: NonNullable<Request['mutation']>[];
  selection?: Selection;
  /** Serve this job's mutant test workers with real forks, bypassing the zygote. */
  bypassZygote?: boolean;
  /**
   * Experimental precollect: true forks the worker from a template that already
   * collected the (single) selected test file; false marks a load-time mutant
   * that must keep the ordinary zygote path. Absent: precollect is off.
   */
  precollect?: boolean;
}
export interface Result {
  lanes?: Omit<Response, 'lanes'>[];
  outcome: 'passed' | 'failed' | 'invalid' | 'timeout' | 'infrastructure-error' | 'skipped';
  tests: { passed: number; failed: number; skipped: number };
  errors: string[];
  coverage?: Coverage;
  /** Baselines only: how long each test file took (import, setup and tests). */
  fileTimings?: { file: string; durationMs: number }[];
  /** Count of resident modules served; required whenever the request had any. */
  resident?: number;
  /**
   * Timeouts only, optional: whether the worker was alive when the job timed out
   * (`test-timeout`, `busy` on the CPU) or looked stalled (`idle`). The CLI's zygote
   * watchdog reruns only timeouts that are not known to be genuine.
   */
  timeoutKind?: 'test-timeout' | 'busy' | 'idle';
}
export type Response = Pick<Request, 'protocol' | 'runId' | 'jobId' | 'generation'> & Result;
