// Compile-only contract: both entry points must narrow unknown requests and
// reject invalid result payloads, including when consumed from TypeScript.
import { envelope, validateRequest } from '../protocol.mjs';
import { validateRequest as validateCompat } from '../protocol.ts';
import type { Request, Response, Result } from '../protocol.ts';

export function checkRuntime(value: unknown, result: Result): Response {
  validateRequest(value);
  // @ts-expect-error The assertion must narrow unknown to Request, not any.
  value.nonexistent;
  const request: Request = value;
  // @ts-expect-error Request discriminants remain strict.
  request.kind = 'other';
  // @ts-expect-error The checked JS API must reject invalid result outcomes.
  envelope(value, { ...result, outcome: 'other' });
  return envelope(request, result);
}

export function checkCompatibility(value: unknown): Request {
  validateCompat(value);
  return value;
}
