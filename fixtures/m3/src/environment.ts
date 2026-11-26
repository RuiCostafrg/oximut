// Reads `globalThis` the way es-toolkit's isBuffer and isPlainObject do. Reading
// the global cannot change what an injected probe resolves, so this file must
// keep precise selection.
export function isBuffer(value: unknown): boolean {
  return (
    typeof globalThis !== 'undefined' &&
    typeof globalThis.Buffer !== 'undefined' &&
    globalThis.Buffer.isBuffer(value) &&
    value !== null
  );
}

export function isPlainObject(value: unknown): boolean {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const proto = Object.getPrototypeOf(value);
  const hasObjectPrototype =
    proto === null || proto === Object.prototype || Object.getPrototypeOf(proto) === null;
  if (!hasObjectPrototype) {
    return false;
  }
  return Object.prototype.toString.call(value) === '[object Object]';
}

export function runtimeKind(): string {
  return globalThis === undefined ? 'unknown' : 'global';
}
