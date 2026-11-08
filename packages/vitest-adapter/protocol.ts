// TypeScript consumers share the checked JavaScript implementation.
export { envelope, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, validateRequest } from './protocol.mjs';

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
  hits: { mutantId: string; tests: string[]; outsideTest: boolean }[];
  reasons: string[];
}

export interface Request {
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
}
export interface Result {
  outcome: 'passed' | 'failed' | 'invalid' | 'timeout' | 'infrastructure-error' | 'skipped';
  tests: { passed: number; failed: number; skipped: number };
  errors: string[];
  coverage?: Coverage;
  /** Count of resident modules served; required whenever the request had any. */
  resident?: number;
}
export type Response = Pick<Request, 'protocol' | 'runId' | 'jobId' | 'generation'> & Result;
