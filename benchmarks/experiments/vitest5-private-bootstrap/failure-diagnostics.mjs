// Error-only W13 experiment diagnostics. No result rewriting or environment capture.
import { appendFileSync } from 'node:fs';
import { types } from 'node:util';
const nativeStackGetter = Object.getOwnPropertyDescriptor(new Error(), 'stack')?.get;

export const LIMITS = Object.freeze({ events: 256, eventBytes: 16384, nodes: 8, depth: 3, strings: 6000 });
function data(object, key) {
  try {
    for (let depth = 0, value = object; value && depth < 3; depth++, value = Object.getPrototypeOf(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (descriptor) return 'value' in descriptor ? descriptor.value : undefined;
    }
  } catch { /* Proxies/accessors do not get to derail error capture. */ }
}
export function diagnosticRecord(stage, details = {}) {
  let remaining = LIMITS.strings, nodes = 0, truncatedStrings = false;
  const seen = new WeakSet();
  const clip = (value, max) => {
    if (typeof value !== 'string') return undefined;
    let text = value.slice(0, Math.min(max, remaining));
    while (text.length && Buffer.byteLength(JSON.stringify(text)) > remaining) text = text.slice(0, Math.floor(text.length/2));
    remaining = Math.max(0, remaining - Buffer.byteLength(JSON.stringify(text)));
    truncatedStrings ||= text.length < value.length;
    return text;
  };
  const error = (value, depth = 0) => {
    if (value === null || typeof value !== 'object') return { value: clip(typeof value === 'string' ? value : typeof value, 512) };
    if (seen.has(value)) return { truncated: 'cycle' };
    if (depth > LIMITS.depth || nodes++ >= LIMITS.nodes) return { truncated: 'depth/node limit' };
    seen.add(value);
    const result = {};
    for (const [key, max] of [['name',80],['message',512],['code',80],['syscall',80],['path',256]]) {
      const text = clip(data(value,key),max); if (text !== undefined) result[key] = text;
    }
    // Node26's own native stack accessor is lazy. Do not call an arbitrary
    // object's stack getter or replace Error.prepareStackTrace/stack itself.
    let stack = data(value,'stack') ?? data(value,'stackStr');
    if (typeof stack !== 'string' && types.isNativeError(value) && nativeStackGetter) {
      try { stack = nativeStackGetter.call(value); }
      catch { result.stackCaptureFailure = 'native stack getter failed'; }
    }
    const capturedStack=clip(stack,1024); if(capturedStack!==undefined)result.stack=capturedStack;
    for (const key of ['errno','signal','actual','expected']) {
      const v = data(value,key);
      if (typeof v === 'number' && Number.isFinite(v) || typeof v === 'boolean' || v === null) result[key] = v;
    }
    const cause = data(value,'cause'); if (cause !== undefined) result.cause = error(cause,depth+1);
    const nested = data(value,'errors');
    if (Array.isArray(nested)) {
      result.errors = [];
      for (let index=0; index<Math.min(nested.length,4); index++) result.errors.push(error(data(nested,String(index)),depth+1));
      if (nested.length>4) result.errorsTruncated = nested.length-4;
    }
    return result;
  };
  const record = { type:'worker-diagnostic', stage:clip(stage,80) };
  for (const key of ['lane','selector','startTime']) {
    const value=clip(data(details,key),256);if(value!==undefined)record[key]=value;
  }
  for (const key of ['pid','parentPid']) {
    const value=data(details,key);if(Number.isSafeInteger(value))record[key]=value;
  }
  const value=data(details,'error');if(value!==undefined)record.error=error(value);
  const errors=data(details,'errors');
  if(Array.isArray(errors)) {
    record.errors=[];
    for(let i=0;i<Math.min(errors.length,4);i++)record.errors.push(error(data(errors,String(i))));
    record.errorCount=errors.length;
  }
  const command=data(details,'observedCommand');
  if(Array.isArray(command)) {
    record.observedCommand=[];
    for(let i=0;i<Math.min(command.length,16);i++)record.observedCommand.push(clip(data(command,String(i)),256));
    if(command.length>16)record.commandTruncated=true;
  }
  if(truncatedStrings)record.stringsTruncated=true;
  return record;
}
export function diagnosticWriter(path, base) {
  let events = 0;
  return (stage, details) => {
    if (events++ >= LIMITS.events) throw new Error('W13 diagnostic event limit exceeded');
    const line=JSON.stringify({controller:base.controller,owner:base.owner,...diagnosticRecord(stage,details)})+'\n';
    if(Buffer.byteLength(line)>LIMITS.eventBytes)throw new Error('W13 diagnostic byte limit exceeded');
    appendFileSync(path,line); // A failed capture throws; never successful evidence.
  };
}
