import type { Vitest } from 'vitest/node';

// One stop-at-first-failure policy for every execution path that owns a controller
// (fresh M1, persistent M2): a mutant's job ends at its first failing test, like
// StrykerJS's default. Baselines never bail, whatever the project config says.

export function bailLimit(kind: 'baseline' | 'mutant'): 0 | 1 {
  return kind === 'mutant' ? 1 : 0;
}

// runFiles clears onCancel listeners before starting workers (5.1.0), so observe the
// public cancellation entry point. The returned function restores it.
export function observeCancellations(vitest: Vitest, cancellations: string[]): () => void {
  const cancelCurrentRun = vitest.cancelCurrentRun;
  vitest.cancelCurrentRun = function (reason) {
    cancellations.push(String(reason));
    return cancelCurrentRun.call(this, reason);
  };
  return () => {
    vitest.cancelCurrentRun = cancelCurrentRun;
  };
}

// The reporter reason is 'interrupted', not the cancellation's 'test-failure'. A bail
// needs both signals and a real test error, so cancellation alone cannot kill a mutant
// and every other error keeps its classification precedence.
export function stoppedAtFirstFailure(input: {
  kind: 'baseline' | 'mutant';
  reason: string;
  cancellations: readonly string[];
  failed: number;
  hasTestError: boolean;
}): boolean {
  return (
    input.kind === 'mutant' &&
    input.reason === 'interrupted' &&
    input.cancellations.length > 0 &&
    input.cancellations.every((reason) => reason === 'test-failure') &&
    input.failed > 0 &&
    input.hasTestError
  );
}
