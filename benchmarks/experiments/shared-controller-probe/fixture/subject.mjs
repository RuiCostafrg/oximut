// Capture at module evaluation, before tests run: this is the resident selector.
export const mutant = process.env.OXIMUT_ACTIVE_MUTANT;
export function value() { return mutant === 'killed' ? 2 : 1; }
