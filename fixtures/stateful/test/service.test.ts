import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { currentCount } from '../src/state.js';
import { initializedMode, isWithinLimit, priceAfterAdjustment } from '../src/service.js';
import { fetchAdjustment } from '../src/dependency.js';

vi.mock('../src/dependency.js', () => ({
  fetchAdjustment: vi.fn(),
}));

const mockedFetchAdjustment = vi.mocked(fetchAdjustment);
let originalMode: string | undefined;

beforeEach(() => {
  originalMode = process.env.OXIMUT_FIXTURE_MODE;
  mockedFetchAdjustment.mockReset();
});

afterEach(() => {
  if (originalMode === undefined) delete process.env.OXIMUT_FIXTURE_MODE;
  else process.env.OXIMUT_FIXTURE_MODE = originalMode;
});

describe('stateful service', () => {
  it('captures the environment at module initialization and starts with a clean singleton', () => {
    expect(currentCount()).toBe(0);
    expect(initializedMode).toBe(process.env.OXIMUT_FIXTURE_MODE ?? 'default');

    let changedMode = `${initializedMode}-changed`;
    while (changedMode === initializedMode) changedMode += '-again';
    process.env.OXIMUT_FIXTURE_MODE = changedMode;
    expect(initializedMode).not.toBe(changedMode);
    expect(currentCount()).toBe(0);
  });

  it('awaits the dependency and advances singleton state once per call', async () => {
    mockedFetchAdjustment.mockResolvedValue(4);
    const startingCount = currentCount();

    await expect(priceAfterAdjustment(10, 'A')).resolves.toBe(15 + startingCount);
    await expect(priceAfterAdjustment(10, 'B')).resolves.toBe(16 + startingCount);
    expect(currentCount()).toBe(startingCount + 2);
    expect(mockedFetchAdjustment).toHaveBeenNthCalledWith(1, 'A');
    expect(mockedFetchAdjustment).toHaveBeenNthCalledWith(2, 'B');
  });

  it('uses an asynchronous adjustment for an inclusive limit check', async () => {
    mockedFetchAdjustment.mockResolvedValue(2);

    await expect(isWithinLimit(8, 10)).resolves.toBe(true);
    await expect(isWithinLimit(9, 10)).resolves.toBe(false);
  });
});
