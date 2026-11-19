import { flushCompileCache } from 'node:module';

// This control has no Vitest, test code, or mutation execution.
process.on('message', (message) => {
  if (message?.__vitest_worker_request__ !== true) return;
  if (message.type === 'stop') flushCompileCache();
  if (message.type === 'start' || message.type === 'stop') {
    process.send({
      __vitest_worker_response__: true,
      type: message.type === 'start' ? 'started' : 'stopped',
    });
  }
});
