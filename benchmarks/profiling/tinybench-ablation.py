#!/usr/bin/env python3
"""Run matched diagnostic captures with and without Vitest's eager tinybench edges."""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
VITEST = ROOT / 'node_modules/vitest'
STATIC = re.compile(rb"^import (?:\{ Bench \} from )?'tinybench';\n", re.MULTILINE)
PROVIDER = b'\t\tconst currentIndex = ++benchIdx;\n\t\tconst tinybench = new Bench({'
LAZY = PROVIDER.replace(b'\t\tconst tinybench', b"\t\tconst { Bench } = await import('tinybench');\n\t\tconst tinybench")
PROBE = '''
import { registerHooks } from 'node:module';
const modules = [];
const hook = registerHooks({ load(url, context, nextLoad) {
  if (url.includes('/node_modules/tinybench/')) modules.push(url);
  return nextLoad(url, context);
} });
// Entry import only. The workload captures use real fork IPC.
process.send = () => true;
await import('./node_modules/vitest/dist/workers/forks.js');
hook.deregister();
console.log(JSON.stringify(modules));
'''


def edits():
    assert json.loads((VITEST / 'package.json').read_text())['version'] == '5.1.0'
    changes = {}
    for path in sorted((VITEST / 'dist').rglob('*.js')):
        original = path.read_bytes()
        if not STATIC.search(original):
            continue
        assert len(STATIC.findall(original)) == 1, path
        modified = STATIC.sub(b'', original)
        if b"import { Bench } from 'tinybench';" in original:
            assert modified.count(PROVIDER) == 1, path
            modified = modified.replace(PROVIDER, LAZY)
        changes[path] = original, modified
    paths = {str(p.relative_to(VITEST)) for p in changes}
    chunks = {p for p in paths if p.startswith('dist/chunks/')}
    assert len(chunks) == 2 and paths - chunks == {
        'dist/index.js', 'dist/worker.js', 'dist/workers/forks.js',
        'dist/workers/threads.js', 'dist/workers/runVmTests.js',
    }, paths
    assert sum(LAZY in b for _, b in changes.values()) == 1
    return changes


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_reachability(changes, output):
    # Run after the captures so the load hook cannot perturb either arm.
    observed = {}
    for arm in ('stock', 'provider-only', 'lazy'):
        for path, (stock, lazy) in changes.items():
            path.write_bytes(lazy if arm == 'lazy' or (arm == 'provider-only' and LAZY in lazy) else stock)
        observed[arm] = json.loads(subprocess.check_output(
            ['node', '--input-type=module', '--eval', PROBE], cwd=ROOT, text=True))
    (output / 'fork-entry-loads.json').write_text(json.dumps(observed, indent=2) + '\n')
    assert len(observed['stock']) == 1 and observed['provider-only'] == observed['stock'] and not observed['lazy']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    assert platform.machine() == 'x86_64' and platform.freedesktop_os_release()['ID'] == 'ubuntu'
    assert len(os.sched_getaffinity(0)) >= 4
    assert subprocess.check_output(['node', '--version'], text=True).strip() == 'v26.3.1'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    changes = edits()
    lock = ROOT / 'package-lock.json'
    lock_digest = sha(lock.read_bytes())
    order = ('stock', 'lazy', 'lazy', 'stock')
    binary = args.binary.resolve()
    metadata = dict(scope='Instrumented diagnostics only', order=order, lock_sha256=lock_digest,
                    binary_sha256=sha(binary.read_bytes()),
                    files={str(p.relative_to(VITEST)): {'stock': sha(a), 'lazy': sha(b)}
                           for p, (a, b) in changes.items()})
    (output / 'experiment.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (output / 'installed-vitest.diff').write_text(''.join(
        ''.join(difflib.unified_diff(a.decode().splitlines(keepends=True), b.decode().splitlines(keepends=True),
                                    f'a/{p.relative_to(VITEST)}', f'b/{p.relative_to(VITEST)}'))
        for p, (a, b) in changes.items()))
    try:
        for index, arm in enumerate(order):
            run = output / f'{index + 1:02}-{arm}'
            run.mkdir()
            for path, sources in changes.items():
                path.write_bytes(sources[arm == 'lazy'])
            commands = [
                ['node', 'benchmarks/profiling/tinybench-smoke.mjs'],
                [sys.executable, 'benchmarks/profile_native.py', '--binary', str(binary),
                 '--worker-phases', '--startup-contract', '--first-collection', '--workload', 'colord-scale',
                 '--concurrency', '4', '--output', str(run / 'capture')],
            ]
            for command, log in zip(commands, ('bench-smoke.log', 'diagnostic.log'), strict=True):
                print(f'{run.name}: {log}', flush=True)
                with (run / log).open('w') as stream:
                    subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
            assert sha(lock.read_bytes()) == lock_digest, 'dependency lock changed'
            for path, sources in changes.items():
                assert path.read_bytes() == sources[arm == 'lazy'], path
        verify_reachability(changes, output)
    finally:
        for path, (stock, _) in changes.items():
            path.write_bytes(stock)


if __name__ == '__main__':
    main()
