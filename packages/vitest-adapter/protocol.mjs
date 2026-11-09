import { isAbsolute, relative, resolve } from 'node:path';

/** @typedef {import('./protocol.ts').Request} Request */
/** @typedef {import('./protocol.ts').Result} Result */
/** @typedef {import('./protocol.ts').Response} Response */

export const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
export const MAX_RESPONSE_BYTES = 1024 * 1024;

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
/** @param {unknown} value @returns {value is string} */
function identity(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 1024;
}
/** @param {unknown} value @returns {asserts value is Request} */
export function validateRequest(value) {
  if (!record(value) || value.protocol !== 1)
    throw new Error('Unsupported or missing protocol (expected 1)');
  if (!identity(value.runId) || !identity(value.jobId)) throw new Error('Invalid runId or jobId');
  if (!Number.isSafeInteger(value.generation) || /** @type {number} */ (value.generation) < 0)
    throw new Error('Invalid generation');
  if (value.kind !== 'baseline' && value.kind !== 'mutant') throw new Error('Invalid job kind');
  for (const field of /** @type {const} */ (['root', 'config'])) {
    if (
      typeof value[field] !== 'string' ||
      !isAbsolute(value[field]) ||
      value[field].includes('\0')
    )
      throw new Error(`${field} must be an absolute path`);
  }
  if (
    !Number.isSafeInteger(value.deadlineMs) ||
    /** @type {number} */ (value.deadlineMs) < 1 ||
    /** @type {number} */ (value.deadlineMs) > 2147483647
  )
    throw new Error('Invalid deadlineMs');
  if (
    value.workers !== undefined &&
    (!Number.isSafeInteger(value.workers) ||
      /** @type {number} */ (value.workers) < 1 ||
      /** @type {number} */ (value.workers) > 1024)
  )
    throw new Error('Invalid workers');
  if (value.lanes !== undefined) {
    if (
      value.kind !== 'mutant' ||
      value.pool !== undefined ||
      value.collectCoverage !== undefined ||
      !Array.isArray(value.lanes) ||
      value.lanes.length < 1 ||
      value.lanes.length > 1024
    )
      throw new Error('Invalid shared controller lanes');
    const ids = new Set();
    for (const job of value.lanes) {
      if (
        !record(job) ||
        job.lanes !== undefined ||
        job.kind !== 'mutant' ||
        job.root !== value.root ||
        job.config !== value.config ||
        job.runId !== value.runId ||
        job.deadlineMs !== value.lanes[0].deadlineMs ||
        job.deadlineMs > value.deadlineMs ||
        job.pool !== undefined ||
        job.collectCoverage !== undefined ||
        ids.has(job.jobId)
      )
        throw new Error('Invalid lane assignment');
      validateRequest(job);
      if (JSON.stringify(job.resident) !== JSON.stringify(value.resident))
        throw new Error('Lanes require identical resident modules');
      ids.add(job.jobId);
    }
  }
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
    const path = relative(/** @type {string} */ (value.root), mutation.file);
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
      validateRequest({
        ...value,
        lanes: undefined,
        kind: 'mutant',
        mutation,
        collectCoverage: undefined,
        resident: undefined,
      });
  }
  if (value.resident !== undefined) {
    if (!Array.isArray(value.resident) || value.resident.length === 0)
      throw new Error('Resident transforms must be a nonempty array');
    const files = new Set();
    for (const mutation of value.resident) {
      validateRequest({
        ...value,
        lanes: undefined,
        kind: 'mutant',
        mutation,
        collectCoverage: undefined,
        resident: undefined,
        selection: undefined,
      });
      const file = resolve(/** @type {{ file: string }} */ (mutation).file);
      if (files.has(file)) throw new Error('Duplicate resident transform file');
      files.add(file);
    }
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
        const path = relative(/** @type {string} */ (value.root), file);
        return path !== '' && path !== '..' && !path.startsWith('../') && !isAbsolute(path);
      }) ||
      !Number.isSafeInteger(selected.selectedTests) ||
      /** @type {number} */ (selected.selectedTests) < 1 ||
      !Number.isSafeInteger(selected.totalTests) ||
      /** @type {number} */ (selected.totalTests) <
        /** @type {number} */ (selected.selectedTests) ||
      (selected.expectedSkipped !== undefined &&
        (!Number.isSafeInteger(selected.expectedSkipped) ||
          /** @type {number} */ (selected.expectedSkipped) < 0 ||
          /** @type {number} */ (selected.expectedSkipped) >
            /** @type {number} */ (selected.selectedTests))) ||
      !Array.isArray(selected.reasons) ||
      !selected.reasons.every((reason) => typeof reason === 'string') ||
      (selected.mode === 'files' &&
        (!selected.files.length || new Set(selected.files).size !== selected.files.length)) ||
      (selected.mode === 'full' &&
        (selected.files.length !== 0 || selected.selectedTests !== selected.totalTests))
    )
      throw new Error('Invalid test selection');
  }
  if (value.pool !== undefined && value.pool !== 'threads') throw new Error('Invalid pool');
}

/**
 * Invalid requests still produce a bounded diagnostic envelope when possible.
 *
 * @param {unknown} value
 * @param {Result} result
 * @returns {Response}
 */
export function envelope(value, result) {
  const item = record(value) ? value : {};
  return {
    protocol: 1,
    runId: identity(item.runId) ? item.runId : '',
    jobId: identity(item.jobId) ? item.jobId : '',
    generation:
      Number.isSafeInteger(item.generation) && /** @type {number} */ (item.generation) >= 0
        ? /** @type {number} */ (item.generation)
        : 0,
    ...result,
    errors: result.errors.slice(0, 8).map((error) => error.slice(0, 2048)),
  };
}
