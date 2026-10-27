#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';

const schema = JSON.parse(await readFile(new URL('../docs/list.schema.json', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ allErrors: true });
const validate = ajv.compile(schema);

export function validateList(list, label = 'list') {
  if (!validate(list)) throw new Error(`${label}: ${ajv.errorsText(validate.errors, { separator: '; ' })}`);
}

// List validation is explicit: normal reports and directory discovery use validate-reports.mjs.
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const inputs = process.argv.slice(2);
    if (!inputs.length) throw new Error('Provide one or more list JSON files');
    for (const input of inputs) {
      let list;
      try { list = JSON.parse(await readFile(input, 'utf8')); }
      catch (error) { throw new Error(`${input}: cannot read JSON: ${error.message}`); }
      validateList(list, input);
    }
    console.log(`Validated ${inputs.length} list(s) against docs/list.schema.json`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
