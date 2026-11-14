import { spawn } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { test } from 'vitest';
test('deadline with descendant', () => {
 const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio:'ignore'});
 appendFileSync("/tmp/oximut-w13-framework-stack-attempt-1/run/events.jsonl", JSON.stringify({type:'descendant',pid:child.pid,ppid:process.pid})+'\n');
 while (true) {}
});
