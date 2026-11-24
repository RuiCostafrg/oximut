'use strict';
// Opt-in diagnostic timeline (OXIMUT_TRACE_TIMELINE=<dir>). Every process that
// loads this file appends JSON lines to <dir>/<role>-<pid>.jsonl. Callers load it
// only when the variable is set, so an untraced run executes none of this.
//
// Clock: `t` is milliseconds since the Unix epoch with sub-millisecond precision,
// `performance.timeOrigin + performance.now()`. Node anchors timeOrigin to the
// realtime clock at process start and advances with the monotonic clock, so lines
// from different processes (and from the Rust scheduler, which writes epoch
// milliseconds from SystemTime) compare directly. A fork() child keeps its
// parent's timeOrigin and the shared monotonic clock, so it stays on the same scale.
const fs = require('node:fs');
// Taken at load, before the precollect quiescence guard (zygote/quiescence.cjs)
// instruments `fs` for a template's collect: the trace's own writes are not the
// test file's and must not make a template ineligible.
const { mkdirSync, openSync, writeSync } = fs;
const path = require('node:path');
const { performance } = require('node:perf_hooks');

const directory = process.env.OXIMUT_TRACE_TIMELINE;
const files = new Map();

function descriptor(role) {
  // process.pid is re-defined in a zygote child, so key by the current pid.
  const name = `${role}-${process.pid}.jsonl`;
  let fd = files.get(name);
  if (fd === undefined) {
    try {
      mkdirSync(directory, { recursive: true });
      fd = openSync(path.join(directory, name), 'a');
    } catch {
      fd = null;
    }
    files.set(name, fd);
  }
  return fd;
}

/** Append one event. Tracing must never fail a run, so write errors are dropped. */
function emit(role, event, fields) {
  const t = performance.timeOrigin + performance.now();
  const fd = directory ? descriptor(role) : null;
  if (fd === null) return;
  try {
    writeSync(fd, `${JSON.stringify({ t, event, role, pid: process.pid, ...fields })}\n`);
  } catch {}
}

module.exports = { emit };
