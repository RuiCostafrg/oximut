// Import-time switches must be selected before this module is evaluated.
export const initialized = true;
let calls = 0;
function next() { return ++calls; }
export function reset() { calls = 0; }
export function count() { return calls; }
export function nested() { return next() + next() * next(); }
export async function asynchronous() { return (await Promise.resolve(3)) + 2; }
export function* sequence() { return (yield 3) + 2; }
export function boundary(value: number) { return value < 3; }
export function unused() { return false; }
