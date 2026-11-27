import { readFileSync } from 'node:fs';
import { registerHooks, stripTypeScriptTypes } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Node's built-in loader refuses TypeScript under node_modules.
// Apply the same type stripping only to the bundled adapter's own modules.
registerHooks({
  load(url, context, nextLoad) {
    if (url.startsWith('file:')) {
      const path = fileURLToPath(url);
      if (path.endsWith('.ts') && dirname(path) === import.meta.dirname) {
        return {
          format: 'module',
          source: stripTypeScriptTypes(readFileSync(path, 'utf8')),
          shortCircuit: true,
        };
      }
    }
    return nextLoad(url, context);
  },
});
