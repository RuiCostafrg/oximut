// Scope the experiment to stock worker entrypoints. The benchmark installs
// this preload only on candidate mutant forks.
if (process.send && process.argv[1]?.endsWith('/vitest/dist/workers/forks.js')
    && (process.env.OXIMUT_BUDGETED === '1' || process.env.OXIMUT_BUNDLE_FIXTURE === '1')) {
  const { readFileSync, writeFileSync } = require('node:fs');
  const { registerHooks } = require('node:module');
  const entries = JSON.parse(readFileSync(process.env.OXIMUT_BUNDLE_ROUTES, 'utf8'));
  registerHooks({
    resolve(specifier, context, nextResolve) {
      const result = nextResolve(specifier, context);
      if (!Object.hasOwn(entries, result.url)) return result;
      if (result.url.endsWith('/vitest/dist/workers/forks.js') && process.env.OXIMUT_BUNDLE_OBSERVED_DIR) {
        writeFileSync(`${process.env.OXIMUT_BUNDLE_OBSERVED_DIR}/${process.pid}.json`,
          JSON.stringify({pid: process.pid, ppid: process.ppid, entry: entries[result.url]}), {flag: 'wx'});
      }
      return {...result, url: entries[result.url]};
    },
  });
}
