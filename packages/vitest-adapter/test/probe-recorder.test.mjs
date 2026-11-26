import assert from 'node:assert/strict';
import test from 'node:test';
import { createRecorder } from '../probe-recorder.ts';

/** A recorder whose running test, deferred tasks and written lines the test controls. */
function harness(options = {}) {
  const state = { test: undefined, lines: [], writes: 0, tasks: [] };
  const recorder = createRecorder({
    currentTest: () => state.test,
    write: (text) => {
      state.writes++;
      for (const line of text.trim().split('\n')) state.lines.push(JSON.parse(line));
    },
    schedule: (task) => state.tasks.push(task),
    ...options,
  });
  const endTurn = () => {
    for (const task of state.tasks.splice(0)) task();
  };
  return { state, recorder, endTurn };
}
const id = (index) => index.toString(16).padStart(64, '0');

test('a repeated hit in one test is recorded once, in one write at the end of the turn', () => {
  const { state, recorder, endTurn } = harness();
  const running = { id: 't1' };
  state.test = running;
  // A hot loop: a million hits of three mutants.
  const ids = [id(0), id(1), id(2)];
  for (let hit = 0; hit < 1_000_000; hit++) recorder.hit(ids[hit % 3]);
  assert.equal(state.writes, 0);
  assert.equal(state.tasks.length, 1);
  endTurn();
  assert.equal(state.writes, 1);
  assert.deepEqual(state.lines, [{ ids: [id(0), id(1), id(2)] }, { test: 't1', hits: [0, 1, 2] }]);
  // Hitting them again in the same test costs nothing, not even another task.
  for (let hit = 0; hit < 1000; hit++) recorder.hit(ids[hit % 3]);
  assert.equal(state.tasks.length, 0);
});

test('a new test closes the previous test’s batch and hits again count for it', () => {
  const { state, recorder, endTurn } = harness();
  const first = { id: 'a' };
  const second = { id: 'b' };
  state.test = first;
  recorder.hit(id(0));
  recorder.hit(id(1));
  state.test = second;
  recorder.hit(id(1));
  recorder.hit(id(2));
  endTurn();
  assert.deepEqual(state.lines, [
    { ids: [id(0), id(1)] },
    { test: 'a', hits: [0, 1] },
    { ids: [id(2)] },
    { test: 'b', hits: [1, 2] },
  ]);
  // Back to the first test: mutant 1 last saw the second test, so the pair is new again
  // (retries and repeats are flagged anyway); mutant 0 last saw this test and stays quiet.
  state.test = first;
  recorder.hit(id(0));
  recorder.hit(id(1));
  endTurn();
  assert.deepEqual(state.lines.at(-1), { test: 'a', hits: [1] });
});

test('hits while no test runs are recorded once per mutant as outside-test', () => {
  const { state, recorder, endTurn } = harness();
  recorder.hit(id(0));
  recorder.hit(id(0));
  recorder.hit(id(1));
  state.test = { id: 't' };
  recorder.hit(id(1));
  state.test = undefined;
  recorder.hit(id(1));
  endTurn();
  assert.deepEqual(state.lines, [
    { ids: [id(0), id(1)] },
    { outside: [0, 1] },
    { test: 't', hits: [1] },
  ]);
});

test('past the pair limit a stream says so once and stops recording', () => {
  const { state, recorder, endTurn } = harness({ maxPairs: 3 });
  state.test = { id: 't' };
  for (let mutant = 0; mutant < 10; mutant++) recorder.hit(id(mutant));
  endTurn();
  state.test = { id: 'u' };
  recorder.hit(id(0));
  endTurn();
  assert.equal(state.lines.filter((line) => line.incomplete).length, 1);
  assert.deepEqual(
    state.lines.find((line) => line.incomplete),
    { incomplete: 'probe-record-limit' },
  );
  const recorded = state.lines.filter((line) => line.hits).flatMap((line) => line.hits);
  assert.deepEqual(recorded, [0, 1, 2]);
});

test('an incomplete note is written after what was hit before it', () => {
  const { state, recorder } = harness();
  state.test = { id: 't' };
  recorder.hit(id(0));
  recorder.incomplete('source-reflection');
  assert.deepEqual(state.lines, [
    { ids: [id(0)] },
    { test: 't', hits: [0] },
    { incomplete: 'source-reflection' },
  ]);
});
