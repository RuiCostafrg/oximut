let counter = 0;

export function currentCount(): number {
  return counter;
}

export function advanceCount(amount: number): number {
  counter += amount;
  return counter;
}
