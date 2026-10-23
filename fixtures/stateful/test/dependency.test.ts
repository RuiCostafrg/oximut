import { expect, it } from 'vitest';
import { fetchAdjustment } from '../src/dependency.js';

it('uses the code length as the default adjustment', async () => {
  await expect(fetchAdjustment('limit')).resolves.toBe(5);
});
