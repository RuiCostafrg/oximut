// Each function is intentionally exercised by a different test identity so
// coverage can narrow ordinary mutants to a strict subset of the suite.
export function enabled(flag: boolean): boolean {
  return flag && true;
}

export function belowLimit(value: number): boolean {
  return value < 10;
}

export function adjusted(value: number): number {
  return value + 2;
}

export function initializedValue(): number {
  return importValue;
}

export function hookValue(): boolean {
  return true;
}

export function hookOnlyValue(): boolean {
  return true;
}

export function suiteHookValue(): boolean {
  return true;
}

export function suiteInitializationValue(): boolean {
  return true;
}

export function concurrentValue(): boolean {
  return true;
}

let sharedState = false;

export function transitionSharedState(): boolean {
  sharedState = true;
  return sharedState;
}

export function readSharedState(): boolean {
  return sharedState;
}

export function neverCalled(): boolean {
  return true;
}

// The imported constant is evaluated while this module is loaded. A mutation
// to it must not be classified as uncovered from test-body coverage alone.
import { importValue } from './top-level';
