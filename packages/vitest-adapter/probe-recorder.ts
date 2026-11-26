/**
 * The coverage probe's bookkeeping, apart from Vitest so that it can be tested alone.
 *
 * The probe sits in the hottest loops of the code under test, so a repeated hit must
 * cost a lookup and a comparison, never an allocation: each mutant has a state object
 * that remembers the last test that hit it, and only the first hit of a (mutant, test)
 * pair is recorded. Records are small integers into a per-stream table of mutant ids,
 * batched, and written before the current turn of the event loop ends (or when the
 * running test changes), so they survive fresh-fork termination without a write per hit.
 *
 * Stream lines (JSON, one per line): `{ids: [...]}` introduces mutants, whose indices
 * run on from the previous `ids` lines; `{test, hits: [index...]}` are the mutants the
 * test hit for the first time; `{outside: [index...]}` are mutants hit while no test
 * ran; `{incomplete: reason}` says attribution cannot be trusted.
 */
export interface RunningTest {
  id: string;
}

export interface RecorderOptions {
  currentTest(): RunningTest | undefined;
  write(text: string): void;
  /** Runs `task` before the current turn of the event loop ends. */
  schedule(task: () => void): void;
  /** Past this many recorded (mutant, test) pairs the stream stops, incomplete. */
  maxPairs?: number;
}

/** The default bound on one stream's recorded pairs. */
export const MAX_PAIRS = 32 * 1024 * 1024;

const NONE = Symbol('none');
interface State {
  index: number;
  /** The last test seen hitting this mutant (`undefined`: none running); `NONE` at first. */
  test: RunningTest | undefined | typeof NONE;
  outside: boolean;
}

export function createRecorder(options: RecorderOptions) {
  const maxPairs = options.maxPairs ?? MAX_PAIRS;
  const states = new Map<string, State>();
  let newIds: string[] = [];
  let pendingTest: RunningTest | undefined;
  let pendingHits: number[] = [];
  let pendingOutside: number[] = [];
  let pairs = 0;
  let overflow = false;
  let scheduled = false;

  function flush(): void {
    scheduled = false;
    let text = '';
    if (newIds.length) text += `${JSON.stringify({ ids: newIds })}\n`;
    if (pendingOutside.length) text += `${JSON.stringify({ outside: pendingOutside })}\n`;
    if (pendingHits.length)
      text += `${JSON.stringify({ test: pendingTest?.id ?? null, hits: pendingHits })}\n`;
    newIds = [];
    pendingOutside = [];
    pendingHits = [];
    if (text) options.write(text);
  }

  function record(state: State, test: RunningTest | undefined): void {
    if (test === undefined) pendingOutside.push(state.index);
    else {
      // The pending hits belong to one test; a new test closes the batch.
      if (pendingTest !== test) {
        if (pendingHits.length) flush();
        pendingTest = test;
      }
      pendingHits.push(state.index);
    }
    if (++pairs >= maxPairs) {
      overflow = true;
      incomplete('probe-record-limit');
    } else if (!scheduled) {
      scheduled = true;
      options.schedule(flush);
    }
  }

  function incomplete(reason: string): void {
    flush();
    options.write(`${JSON.stringify({ incomplete: reason })}\n`);
  }

  function hit(mutantId: string): void {
    const test = options.currentTest();
    let state = states.get(mutantId);
    if (state === undefined) {
      if (overflow) return;
      state = { index: states.size, test: NONE, outside: false };
      states.set(mutantId, state);
      newIds.push(mutantId);
    }
    if (state.test === test || overflow) return;
    state.test = test;
    if (test === undefined) {
      if (state.outside) return;
      state.outside = true;
    }
    record(state, test);
  }

  return { hit, flush, incomplete };
}
