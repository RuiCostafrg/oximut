import { existsSync, readdirSync, readFileSync } from 'node:fs';

/**
 * Why a job timed out, as far as the adapter can tell: `test-timeout` is Vitest's own
 * timeout, reported by a worker that was alive and answering; `busy` and `idle` say what
 * a worker stopped at the job deadline was doing. Absent means unknown.
 *
 * What a lane worker was doing when its deadline stopped it.
 *
 * Under the zygote a timeout is ambiguous: the mutant may really loop forever (the
 * code under test spins), or the forked worker may have deadlocked (a V8 worker
 * thread condvar copied by fork() once did, see docs/zygote.md). The CLI reruns the
 * second kind on an ordinary worker and has no reason to rerun the first. They differ
 * in one observable way: a looping worker keeps burning CPU to the last millisecond,
 * a deadlocked one sleeps on a futex. Progress events are no help: a synchronous loop
 * blocks the very event loop that would send them, and the deadlock hit during
 * garbage collection, which happens after tests started just as well.
 */
export type TimeoutKind = 'test-timeout' | 'busy' | 'idle';

/** USER_HZ: the unit of /proc/<pid>/stat CPU times. 100 on every Linux ABI we run on. */
export const CLOCK_TICKS_PER_SECOND = 100;
/** A looping worker may share its core, so far less than a full core still counts as busy. */
export const BUSY_FRACTION = 0.2;
/** Shorter than this the tick granularity (10 ms) says too little. */
export const MIN_WINDOW_MS = 50;
const MAX_WINDOW_MS = 2000;

/**
 * How long before its deadline a lane's CPU time is first read: a fifth of the
 * deadline, between 100 ms and two seconds, never more than the deadline itself.
 */
export function sampleWindowMs(deadlineMs: number): number {
  return Math.min(deadlineMs, MAX_WINDOW_MS, Math.max(100, deadlineMs / 5));
}

/** User plus system CPU ticks of a process, or undefined when it cannot be read. */
export function cpuTicks(pid: number | undefined): number | undefined {
  if (pid === undefined || !Number.isSafeInteger(pid) || pid <= 0) return undefined;
  try {
    // "pid (comm) state ppid ...": comm may hold spaces and parentheses, so split
    // after its last parenthesis. utime and stime are fields 14 and 15.
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    const ticks = Number(fields[11]) + Number(fields[12]);
    return Number.isFinite(ticks) ? ticks : undefined;
  } catch {
    return undefined;
  }
}

export interface CpuSample {
  ticks: number;
  /** performance.now() when the ticks were read. */
  at: number;
}

/**
 * `busy` when the worker used at least BUSY_FRACTION of a core between two readings,
 * `idle` when it did not, undefined when the readings say too little to tell (a
 * process that could not be read, or two readings taken too close together).
 */
export function classifyCpu(
  before: CpuSample | undefined,
  after: CpuSample | undefined,
): 'busy' | 'idle' | undefined {
  if (!before || !after) return undefined;
  const elapsedMs = after.at - before.at;
  if (!(elapsedMs >= MIN_WINDOW_MS) || after.ticks < before.ticks) return undefined;
  const cpuMs = ((after.ticks - before.ticks) * 1000) / CLOCK_TICKS_PER_SECOND;
  return cpuMs >= elapsedMs * BUSY_FRACTION ? 'busy' : 'idle';
}

/** Parent pid of every process, from /proc/<pid>/stat. */
function parentMap(): Map<number, number[]> {
  const children = new Map<number, number[]>();
  let entries: string[] = [];
  try {
    entries = readdirSync('/proc');
  } catch {}
  for (const entry of entries) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const stat = readFileSync(`/proc/${entry}/stat`, 'utf8');
      const parent = Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1]);
      const list = children.get(parent);
      if (list) list.push(Number(entry));
      else children.set(parent, [Number(entry)]);
    } catch {}
  }
  return children;
}

/**
 * Direct children of a process from the kernel's per-task lists (cheap), or
 * undefined when the kernel does not keep them (CONFIG_PROC_CHILDREN is optional).
 */
function listedChildren(pid: number): number[] | undefined {
  const children: number[] = [];
  try {
    for (const task of readdirSync(`/proc/${pid}/task`))
      for (const child of readFileSync(`/proc/${pid}/task/${task}/children`, 'utf8')
        .split(' ')
        .filter(Boolean))
        children.push(Number(child));
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'ENOENT' && !existsSync(`/proc/${pid}`)
      ? []
      : undefined;
  }
  return children;
}

/** The process and everything below it, found before any of it is signalled. */
export function processTree(pid: number): number[] {
  const tree = [pid];
  let scanned: Map<number, number[]> | undefined;
  for (let index = 0; index < tree.length; index++) {
    const direct = scanned === undefined ? listedChildren(tree[index]) : undefined;
    const children = direct ?? (scanned ??= parentMap()).get(tree[index]) ?? [];
    for (const child of children) if (!tree.includes(child)) tree.push(child);
  }
  return tree;
}

/**
 * SIGKILL a worker and every process it started, so that whatever it was holding
 * (a runaway loop's memory included) is released now rather than when a later
 * sweep finds the orphans. The tree is read first: once the parent is gone its
 * children are reparented and can no longer be found from it.
 */
export function killTree(pid: number, kill: (pid: number) => void = signalKill): void {
  const tree = processTree(pid);
  // Parents first would let a fork race past the sweep; the leaves go first.
  for (const target of tree.reverse()) kill(target);
}

function signalKill(pid: number): void {
  try {
    process.kill(pid, 'SIGKILL');
  } catch {}
}
