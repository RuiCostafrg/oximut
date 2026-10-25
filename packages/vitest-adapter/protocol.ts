import { isAbsolute, relative } from 'node:path';

export const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
export const MAX_RESPONSE_BYTES = 1024 * 1024;

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
  mutation: null | { id: string; file: string; code: string; map: Record<string, unknown> };
  collectCoverage?: NonNullable<Request['mutation']>[];
  selection?: Selection;
}
export interface Result {
  outcome: 'passed' | 'failed' | 'invalid' | 'timeout' | 'infrastructure-error' | 'skipped';
  tests: { passed: number; failed: number; skipped: number };
  errors: string[];
  coverage?: Coverage;
}
export type Response = Pick<Request, 'protocol' | 'runId' | 'jobId' | 'generation'> & Result;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function identity(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 1024;
}
export function validateRequest(value: unknown): asserts value is Request {
  if (!record(value) || value.protocol !== 1)
    throw new Error('Unsupported or missing protocol (expected 1)');
  if (!identity(value.runId) || !identity(value.jobId)) throw new Error('Invalid runId or jobId');
  if (!Number.isSafeInteger(value.generation) || (value.generation as number) < 0)
    throw new Error('Invalid generation');
  if (value.kind !== 'baseline' && value.kind !== 'mutant') throw new Error('Invalid job kind');
  for (const field of ['root', 'config'] as const) {
    if (
      typeof value[field] !== 'string' ||
      !isAbsolute(value[field]) ||
      value[field].includes('\0')
    )
      throw new Error(`${field} must be an absolute path`);
  }
  if (
    !Number.isSafeInteger(value.deadlineMs) ||
    (value.deadlineMs as number) < 1 ||
    (value.deadlineMs as number) > 2147483647
  )
    throw new Error('Invalid deadlineMs');
  if (value.kind === 'baseline') {
    if (value.mutation !== null) throw new Error('Baseline mutation must be null');
  } else {
    const mutation = value.mutation;
    if (
      !record(mutation) ||
      !identity(mutation.id) ||
      typeof mutation.code !== 'string' ||
      typeof mutation.file !== 'string' ||
      !isAbsolute(mutation.file) ||
      mutation.file.includes('\0') ||
      !record(mutation.map)
    )
      throw new Error('Invalid mutation');
    const path = relative(value.root as string, mutation.file);
    if (!path || path === '..' || path.startsWith('../') || isAbsolute(path))
      throw new Error('Mutation file must be inside root');
    if (
      mutation.map.version !== 3 ||
      !Array.isArray(mutation.map.sources) ||
      !mutation.map.sources.every((source) => typeof source === 'string') ||
      typeof mutation.map.mappings !== 'string'
    )
      throw new Error('Invalid version 3 source map');
  }
  if (value.collectCoverage !== undefined) {
    if (value.kind !== 'baseline' || !Array.isArray(value.collectCoverage))
      throw new Error('Coverage collection requires baseline probe transforms');
    for (const mutation of value.collectCoverage)
      validateRequest({ ...value, kind: 'mutant', mutation, collectCoverage: undefined });
  }
  if (value.selection !== undefined) {
    const selected = value.selection;
    if (
      !record(selected) ||
      value.kind !== 'mutant' ||
      !['files', 'full'].includes(String(selected.mode)) ||
      !Array.isArray(selected.files) ||
      !selected.files.every((file) => {
        if (typeof file !== 'string' || !isAbsolute(file) || file.includes('\0')) return false;
        const path = relative(value.root as string, file);
        return path !== '' && path !== '..' && !path.startsWith('../') && !isAbsolute(path);
      }) ||
      !Number.isSafeInteger(selected.selectedTests) ||
      (selected.selectedTests as number) < 1 ||
      !Number.isSafeInteger(selected.totalTests) ||
      (selected.totalTests as number) < (selected.selectedTests as number) ||
      (selected.expectedSkipped !== undefined &&
        (!Number.isSafeInteger(selected.expectedSkipped) ||
          (selected.expectedSkipped as number) < 0 ||
          (selected.expectedSkipped as number) > (selected.selectedTests as number))) ||
      !Array.isArray(selected.reasons) ||
      !selected.reasons.every((reason) => typeof reason === 'string') ||
      (selected.mode === 'files' &&
        (!selected.files.length || new Set(selected.files).size !== selected.files.length)) ||
      (selected.mode === 'full' &&
        (selected.files.length !== 0 || selected.selectedTests !== selected.totalTests))
    )
      throw new Error('Invalid test selection');
  }
}

/** Invalid requests still produce a bounded diagnostic envelope when possible. */
export function envelope(value: unknown, result: Result): Response {
  const item = record(value) ? value : {};
  return {
    protocol: 1,
    runId: identity(item.runId) ? item.runId : '',
    jobId: identity(item.jobId) ? item.jobId : '',
    generation:
      Number.isSafeInteger(item.generation) && (item.generation as number) >= 0
        ? (item.generation as number)
        : 0,
    ...result,
    errors: result.errors.slice(0, 8).map((error) => error.slice(0, 2048)),
  };
}
