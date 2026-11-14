const selector = process.env.OXIMUT_ACTIVE_MUTANT;
export const observed = selector;
let calls = 0;
export const count = () => calls++;
export const add = (a: number, b: number) => selector === 'A' ? a + b + 1 : a + b;
export const multiply = (a: number, b: number) => selector === 'B' ? a * b + 1 : a * b;
