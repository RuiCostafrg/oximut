export function clamp(value: number, lower: number, upper: number): number {
  if (value < lower) return lower;
  if (value > upper) return upper;
  return value;
}

export function classifyScore(score: number): string {
  if (score < 0) return 'negative';
  if (score >= 90) return 'excellent';
  if (score >= 60) return 'passing';
  return 'failing';
}

export function applyDiscount(price: number, percent: number): number {
  if (price <= 0 || percent <= 0) return price;
  if (percent >= 100) return 0;
  return price - (price * percent) / 100;
}

export function shippingCost(weight: number, expedited: boolean): number {
  if (weight <= 0) return 0;
  const base = weight <= 2 ? 5 : weight <= 10 ? 10 : 18;
  return expedited ? base + 7 : base;
}

export function canAccess(
  age: number,
  hasPass: boolean,
  isSuspended: boolean,
): boolean {
  return age >= 18 && hasPass && !isSuspended;
}

export function describeRange(value: number): string {
  if (value < 10) return 'small';
  if (value <= 20) return 'medium';
  return 'large';
}

export function combineFlags(primary: boolean, secondary: boolean): boolean {
  return primary || secondary;
}
