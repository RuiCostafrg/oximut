#!/usr/bin/env python3
"""One bounded structural capture, with prerequisite failures and exact Node provenance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def capture(args):
    output = Path(args.output).resolve()
    output.mkdir()  # Never overwrite earlier evidence.
    result = {'status': 'incomplete', 'performance': 'structural only; no scored resources', 'steps': []}
    node = str(Path(args.node).resolve())
    env = dict(os.environ, PATH=str(Path(node).parent) + os.pathsep + os.environ['PATH'])

    def step(name, command, timeout):
        with (output / (name + '.stdout.log')).open('wb') as stdout, (output / (name + '.stderr.log')).open('wb') as stderr:
            try:
                run = subprocess.run(command, cwd=REPO, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
                status = {'argv': command, 'exitCode': run.returncode}
            except subprocess.TimeoutExpired:
                status = {'argv': command, 'timeout': timeout}
        (output / (name + '.status.json')).write_text(json.dumps(status, indent=2) + '\n')
        result['steps'].append({'name': name, **status})
        if status.get('exitCode') != 0:
            raise RuntimeError(f'{name} failed: {status}')

    try:
        (output / 'commit.txt').write_bytes(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO))
        # Include exact dirty executable sources too, not just the committed base.
        with tarfile.open(output / 'source.tar.gz', 'w:gz') as archive:
            for path in [HERE, REPO / 'package.json', REPO / 'package-lock.json', REPO / '.node-version']:
                archive.add(path, arcname=str(path.relative_to(REPO)))
            workflow = REPO / '.github/workflows/w13-private-bootstrap.yml'
            if workflow.exists():
                archive.add(workflow, arcname=str(workflow.relative_to(REPO)))
        runtime = json.loads(subprocess.check_output([node, '-p', 'JSON.stringify({version:process.version,arch:process.arch,platform:process.platform,execPath:process.execPath})'], env=env))
        assert runtime['version'] == 'v26.3.1' and runtime['platform'] == 'linux'
        assert runtime['arch'] in ['arm64', 'x64']
        version = runtime['version']
        filename = f'node-{version}-linux-{runtime["arch"]}.tar.xz'
        base = f'https://nodejs.org/dist/{version}/'
        with urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=30) as response:
            sums = response.read()
        (output / 'upstream-SHASUMS256.txt').write_bytes(sums)
        expected = next(line.split()[0] for line in sums.decode().splitlines() if line.endswith('  ' + filename))
        with tempfile.TemporaryDirectory(prefix='w13-node-archive-') as temp:
            path = Path(args.archive).resolve() if args.archive else Path(temp) / filename
            if not args.archive:
                with urllib.request.urlopen(base + filename, timeout=60) as response, path.open('wb') as target:
                    shutil.copyfileobj(response, target)
            assert sha(path) == expected, 'Node distribution archive does not match upstream SHA-256'
            with tarfile.open(path) as archive:
                member = archive.extractfile(f'node-{version}-linux-{runtime["arch"]}/bin/node')
                digest = hashlib.sha256()
                while chunk := member.read(1024 * 1024):
                    digest.update(chunk)
            assert digest.hexdigest() == sha(runtime['execPath']), 'Executable differs from verified upstream archive member'
        result['runtime'] = {**runtime, 'executableSha256': digest.hexdigest(), 'archiveURL': base + filename, 'archiveSha256': expected, 'checksumURL': base + 'SHASUMS256.txt', 'checksumFileSha256': sha(output / 'upstream-SHASUMS256.txt'), 'verification': 'HTTPS upstream checksum -> archive bytes -> extracted bin/node bytes == executed binary'}
        (output / 'runtime-provenance.json').write_text(json.dumps(result['runtime'], indent=2) + '\n')
        if args.install:
            step('install', ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], 180)
        step('focused', [node, '--experimental-import-meta-resolve', '--test', '--test-concurrency=1', str(HERE / 'test-probe.mjs'), str(HERE / 'test-map-registration.mjs')], 60)
        step('matrix', [node, '--experimental-import-meta-resolve', str(HERE / 'probe.mjs'), str(output / 'run')], 360)
        evidence = json.loads((output / 'run/evidence.json').read_text())
        assert evidence['status'] == 'complete local structural matrix passed; native qualification pending'
        assert evidence['node']['sha256'] == result['runtime']['executableSha256']
        result['status'] = 'complete structural qualification passed on this host; no performance qualification'
    except Exception as error:
        result['status'] = 'failed'; result['error'] = repr(error)
    finally:
        (output / 'capture.json').write_text(json.dumps(result, indent=2) + '\n')
        files = [path for path in sorted(output.rglob('*')) if path.is_file() and not path.is_symlink() and 'node_modules' not in path.parts and path.name != 'SHA256SUMS']
        (output / 'SHA256SUMS').write_text(''.join(f'{sha(path)}  {path.relative_to(output)}\n' for path in files))
    print(json.dumps({'output': str(output), 'status': result['status'], 'error': result.get('error')}))
    return 0 if result['status'].startswith('complete structural') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--node', default=shutil.which('node'), required=False)
    parser.add_argument('--archive', help='Existing distribution archive; still verified against fresh upstream checksums')
    parser.add_argument('--install', action='store_true')
    raise SystemExit(capture(parser.parse_args()))
