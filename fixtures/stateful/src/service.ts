import { fetchAdjustment } from './dependency.js';
import { advanceCount } from './state.js';

export const initializedMode = process.env.OXIMUT_FIXTURE_MODE ?? 'default';

export async function priceAfterAdjustment(
  base: number,
  code: string,
): Promise<number> {
  const adjustment = await fetchAdjustment(code);
  const sequence = advanceCount(1);
  return base + adjustment + sequence;
}

export async function isWithinLimit(value: number, limit: number): Promise<boolean> {
  const adjustment = await fetchAdjustment('limit');
  return value + adjustment <= limit;
}
