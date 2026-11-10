import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { isAbsolute, resolve, sep } from 'node:path';

/** The part of a Vite environment the share key depends on. */
export interface TransformEnvironment {
  readonly name: string;
}

/** Options Vitest passes to `ModuleTransformService.fetch`. */
export interface TransformOptions {
  cached?: boolean;
  startOffset?: number;
}

/** A transformed module Vitest can hand to another lane. */
export interface TransformEntry {
  id: string;
  file: string;
  tmp?: unknown;
  [key: string]: unknown;
}

/** The subset of `ModuleTransformService` this module replaces. */
export interface TransformService {
  fetch: (
    url: string,
    importer: string | undefined,
    environment: TransformEnvironment,
    makeTmpCopies?: boolean,
    options?: TransformOptions,
    otelCarrier?: unknown,
  ) => Promise<unknown>;
}

export interface TransformShareStats {
  hits: number;
  misses: number;
  bypasses: number;
}

interface Slot {
  result: unknown;
  entry?: TransformEntry;
}

interface Request {
  original: TransformService['fetch'];
  prefixes: readonly string[];
  ns: string;
  url: string;
  importer: string | undefined;
  environment: TransformEnvironment;
  makeTmpCopies: boolean | undefined;
  options: TransformOptions | undefined;
  otelCarrier: unknown;
}

const ROOT = '/';
const ALLOWED_OPTIONS = new Set(['cached', 'startOffset']);
const NODE_MODULES = 'node_modules';

/**
 * Shares module transform results between the lane projects of one controller.
 * Every lane has its own Vite server and module graph, so without a share each
 * source and test file is transformed once per lane on every batch. Results are
 * only shared when their configs cannot change transformed output; anything
 * uncertain is left to the lane's own transform service.
 */
export class TransformShare {
  readonly stats: TransformShareStats = { hits: 0, misses: 0, bypasses: 0 };
  private entries = new Map<string, Promise<Slot>>();
  private services = new WeakSet<object>();
  private clears = 0;

  /** Number of `clear()` calls, exposed so tests can observe invalidation. */
  get generation(): number {
    return this.clears;
  }

  clear(): void {
    // Any computation started before this call holds the previous map and can
    // only ever write into it, so it can never repopulate the new generation.
    this.entries = new Map();
    this.clears++;
  }

  wrap(service: TransformService, ns: string, allowedPrefixes: readonly string[]): void {
    if (this.services.has(service))
      throw new Error('Transform service is already wrapped for sharing');
    this.services.add(service);
    const original = service.fetch.bind(service) as TransformService['fetch'];
    const prefixes = allowedPrefixes.map(normalizePrefix);
    service.fetch = (url, importer, environment, makeTmpCopies, options, otelCarrier) =>
      this.request({
        original,
        prefixes,
        ns,
        url,
        importer,
        environment,
        makeTmpCopies,
        options,
        otelCarrier,
      });
  }

  private async request(call: Request): Promise<unknown> {
    if (!shareable(call.environment, call.options)) {
      this.stats.bypasses++;
      return callOriginal(call);
    }
    const key = JSON.stringify([
      call.ns,
      call.environment.name,
      !!call.makeTmpCopies,
      call.url,
      call.options?.startOffset ?? null,
      call.url[0] === ROOT ? null : (call.importer ?? null),
    ]);
    // Capture the map: a clear() while this request is in flight must not let it
    // write into the replacement map.
    const entries = this.entries;
    const pending = entries.get(key);
    if (pending) {
      let slot: Slot | undefined;
      try {
        slot = await pending;
      } catch {
        // A failed computation is never shared: use this lane's own service.
      }
      const entry = slot?.entry;
      if (entry && (typeof entry.tmp !== 'string' || existsSync(entry.tmp))) {
        this.stats.hits++;
        // Callers and plugins may annotate results; never expose the stored one.
        return { ...entry };
      }
      if (entry) entries.delete(key);
      this.stats.bypasses++;
      return callOriginal(call);
    }
    this.stats.misses++;
    const promise = callOriginal(call).then(
      (result): Slot => {
        const entry = this.store(result, call.prefixes);
        if (!entry) entries.delete(key);
        return { result, entry };
      },
      (error: unknown) => {
        // Waiters fall back to their own service; the first requester keeps its error.
        entries.delete(key);
        throw error;
      },
    );
    entries.set(key, promise);
    return (await promise).result;
  }

  private store(result: unknown, prefixes: readonly string[]): TransformEntry | undefined {
    if (typeof result !== 'object' || result === null || Array.isArray(result)) return undefined;
    const entry = result as Record<string, unknown>;
    // Externalized modules carry no transform; cache sentinels carry no code.
    if ('externalize' in entry || entry.cache === true) return undefined;
    const { id, file } = entry;
    if (typeof id !== 'string' || typeof file !== 'string') return undefined;
    // Virtual files have no bytes every lane could read.
    if (!isAbsolute(file) || file.includes('\0') || file.startsWith('virtual:')) return undefined;
    if (file.split(/[\\/]/).includes(NODE_MODULES)) return undefined;
    if (!prefixes.some((prefix) => isUnder(file, prefix))) return undefined;
    return entry as TransformEntry;
  }
}

function callOriginal(call: Request): Promise<unknown> {
  return call.original(
    call.url,
    call.importer,
    call.environment,
    call.makeTmpCopies,
    call.options,
    call.otelCarrier,
  );
}

function shareable(
  environment: TransformEnvironment,
  options: TransformOptions | undefined,
): boolean {
  if (typeof environment !== 'object' || environment === null) return false;
  if (typeof environment.name !== 'string') return false;
  if (options === undefined) return true;
  if (typeof options !== 'object' || options === null || options.cached) return false;
  return Object.keys(options).every((key) => ALLOWED_OPTIONS.has(key));
}

function normalizePrefix(prefix: string): string {
  const absolute = resolve(prefix);
  return absolute.length > 1 && absolute.endsWith(sep) ? absolute.slice(0, -sep.length) : absolute;
}

function isUnder(file: string, prefix: string): boolean {
  return file === prefix || file.startsWith(`${prefix}${sep}`);
}

/** The resolved configs a project's transform output depends on. */
export interface ProjectLike {
  vite?: { config?: unknown };
  config?: unknown;
}

const LANE_TEST_KEYS = ['name', 'env', 'pool', 'runner'] as const;
const MAX_DEPTH = 12;

/**
 * A stable digest of everything that can change a project's transformed output.
 * Per-lane identity, runtime environment and storage locations are excluded: two
 * lanes with the same fingerprint may serve each other's transform results.
 */
export function fingerprint(project: ProjectLike): string {
  if (typeof project !== 'object' || project === null || project.vite?.config === undefined)
    throw new Error('Project has no resolved Vite config');
  if (project.config === undefined) throw new Error('Project has no resolved config');
  return createHash('sha256')
    .update(stable([sanitizeVite(project.vite.config), sanitizeProject(project.config)]))
    .digest('hex');
}

function sanitizeVite(config: unknown): unknown {
  if (!isRecord(config)) return config;
  const copy: Record<string, unknown> = { ...config };
  // Storage-only: where Vite and Vitest keep caches and how HMR authenticates.
  // Neither is part of an SSR transform result.
  delete copy.cacheDir;
  delete copy.webSocketToken;
  if ('test' in copy) copy.test = sanitizeTest(copy.test);
  if (isRecord(copy.inlineConfig)) {
    const inline = copy.inlineConfig;
    copy.inlineConfig =
      'test' in inline ? { ...inline, test: without(inline.test, LANE_TEST_KEYS) } : inline;
  }
  return copy;
}

function sanitizeProject(config: unknown): unknown {
  const copy = without(config, ['name', 'env', 'pool', 'runner', 'execArgv']);
  if (!isRecord(copy)) return copy;
  const next: Record<string, unknown> = { ...copy };
  // Test ordering and cache location cannot change transformed bytes.
  if ('sequence' in next) next.sequence = without(next.sequence, ['seed']);
  if ('cache' in next) next.cache = without(next.cache, ['dir']);
  return next;
}

function sanitizeTest(test: unknown): unknown {
  const copy = without(test, LANE_TEST_KEYS);
  if (!isRecord(copy)) return copy;
  const next: Record<string, unknown> = { ...copy };
  if ('sequence' in next) next.sequence = without(next.sequence, ['seed']);
  if ('cache' in next) next.cache = without(next.cache, ['dir']);
  return next;
}

function without(object: unknown, keys: readonly string[]): unknown {
  if (!isRecord(object)) return object;
  const copy: Record<string, unknown> = { ...object };
  for (const key of keys) delete copy[key];
  return copy;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stable(value: unknown): string {
  return serialize(value, [], 0);
}

function serialize(value: unknown, stack: object[], depth: number): string {
  if (depth > MAX_DEPTH) return '"[depth]"';
  switch (typeof value) {
    case 'undefined':
      return 'undefined';
    case 'string':
      return JSON.stringify(value);
    case 'number':
      return Object.is(value, -0) ? '-0' : JSON.stringify(value);
    case 'bigint':
      return `${value}n`;
    case 'boolean':
      return String(value);
    case 'symbol':
      return JSON.stringify(String(value));
    case 'function':
      return JSON.stringify(Function.prototype.toString.call(value));
    case 'object':
      break;
  }
  if (value === null) return 'null';
  const object = value as object;
  if (object instanceof RegExp) return JSON.stringify(String(object));
  if (stack.includes(object)) return '"[circular]"';
  stack.push(object);
  try {
    if (object instanceof Map)
      return `Map(${[...object]
        .map(
          ([key, item]) =>
            `[${serialize(key, stack, depth + 1)},${serialize(item, stack, depth + 1)}]`,
        )
        .join(',')})`;
    if (object instanceof Set)
      return `Set(${[...object].map((item) => serialize(item, stack, depth + 1)).join(',')})`;
    if (Array.isArray(object)) {
      if (object.length && object.every(isPlugin))
        return `plugins(${object.map((plugin) => JSON.stringify(plugin.name)).join(',')})`;
      return `[${object.map((item) => serialize(item, stack, depth + 1)).join(',')}]`;
    }
    const keys = Object.keys(object).sort();
    return `{${keys
      .map(
        (key) =>
          `${JSON.stringify(key)}:${serialize(
            (object as Record<string, unknown>)[key],
            stack,
            depth + 1,
          )}`,
      )
      .join(',')}}`;
  } finally {
    stack.pop();
  }
}

function isPlugin(value: unknown): value is { name: string } {
  return isRecord(value) && typeof value.name === 'string';
}
