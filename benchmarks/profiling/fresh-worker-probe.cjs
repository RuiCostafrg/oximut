// Shared, replay-only checkpoint before either worker's ESM entrypoint.
const checkpoint = () => ({
  ns: String(process.hrtime.bigint()),
  cpuUs: process.cpuUsage(),
});
const preload = checkpoint();
let beforeStart;
process.prependListener('message', (message) => {
  if (message?.__vitest_worker_request__ && message.type === 'start') {
    beforeStart ??= checkpoint();
  }
});
const send = process.send;
process.send = function (message, ...args) {
  if (message?.__vitest_worker_response__) {
    message = {...message, diagnostic: {preload, beforeStart, response: checkpoint()}};
  }
  return Reflect.apply(send, this, [message, ...args]);
};
