// A parameter named `globalThis` shadows the global, so an injected probe could
// resolve to it. This file cannot be probed and its mutants must run the whole
// suite; the other files in the run keep precise selection.
export function shadowed(globalThis: { count: number }): number {
  return globalThis.count + 1;
}
