#!/usr/bin/env node
import { readFile, readdir, stat } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';

const schema = JSON.parse(await readFile(new URL('../docs/report.schema.json', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ allErrors: true });
const validate = ajv.compile(schema);

// Explicit files are always validated, regardless of their names or contents.
// Directories recursively select report.json and *-report.json only.
async function collect(input) {
  const info = await stat(input);
  if (info.isFile()) return [input];
  if (!info.isDirectory()) throw new Error(`${input}: expected a file or directory`);
  const paths = [];
  for (const entry of await readdir(input, { withFileTypes: true })) {
    const path = join(input, entry.name);
    if (entry.isDirectory()) paths.push(...await collect(path));
    else if (entry.isFile() && /^(?:report|.+-report)\.json$/.test(entry.name)) paths.push(path);
  }
  return paths;
}

export async function validateReports(inputs) {
  if (!inputs.length) throw new Error('Provide one or more report files or directories');
  let count = 0;
  for (const input of inputs) {
    const files = await collect(resolve(input));
    if (!files.length) throw new Error(`${input}: no matching report files`);
    for (const file of files.sort()) {
      let report;
      try { report = JSON.parse(await readFile(file, 'utf8')); }
      catch (error) { throw new Error(`${file}: cannot read JSON: ${error.message}`); }
      if (!validate(report)) throw new Error(`${file}: ${ajv.errorsText(validate.errors, { separator: '; ' })}`);
      count++;
    }
  }
  return count;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const count = await validateReports(process.argv.slice(2));
    console.log(`Validated ${count} report(s) against docs/report.schema.json`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
