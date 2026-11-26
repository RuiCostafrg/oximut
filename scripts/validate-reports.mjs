#!/usr/bin/env node
import { readFile, readdir, stat } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';

const schema = JSON.parse(await readFile(new URL('../docs/report.schema.json', import.meta.url), 'utf8'));
const ajv = new Ajv2020({ allErrors: true });
const validate = ajv.compile(schema);

const FAMILIES = ['arithmetic', 'assignment-operator', 'boolean-literal', 'comparison-boundary', 'equality-operator', 'logical-operator', 'unary-operator', 'update-operator'];

// Cross-field rules the JSON schema cannot express. Consumers such as ts-crap
// reject reports that break them, so CI checks every emitted report too.
function checkBindings(report) {
  if (report.sources) {
    const counts = new Map();
    for (const source of report.sources) {
      if (counts.has(source.file)) throw new Error(`sources lists ${source.file} twice`);
      counts.set(source.file, 0);
    }
    for (const mutant of report.mutants) {
      if (!counts.has(mutant.file)) throw new Error(`mutant ${mutant.id} is in ${mutant.file}, which sources does not list`);
      counts.set(mutant.file, counts.get(mutant.file) + 1);
    }
    for (const source of report.sources) {
      if (counts.get(source.file) !== source.mutants) {
        throw new Error(`sources says ${source.file} has ${source.mutants} mutant(s); the report has ${counts.get(source.file)}`);
      }
    }
  }
  if (report.mutators) {
    const { selection, families } = report.mutators;
    if (new Set(families).size !== families.length) throw new Error('mutators.families repeats a family');
    if (selection === 'all' && families.length !== FAMILIES.length) throw new Error('mutators.selection is "all" but families is not every family');
    if (selection === 'subset' && !families.length) throw new Error('mutators.selection is "subset" but families is empty');
    const unselected = report.mutants.find(mutant => !families.includes(mutant.operator));
    if (unselected) throw new Error(`mutant ${unselected.id} uses ${unselected.operator}, which mutators.families does not list`);
  }
}

export function validateReport(report, label = 'report') {
  if (!validate(report)) throw new Error(`${label}: ${ajv.errorsText(validate.errors, { separator: '; ' })}`);
  try { checkBindings(report); }
  catch (error) { throw new Error(`${label}: ${error.message}`); }
}

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
      validateReport(report, file);
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
