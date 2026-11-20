#!/usr/bin/env bash
set -euo pipefail
run_id="$1"
dest="$2"
repo="repos/RuiCostafrg/oximut"
mkdir -p "$dest"
gh api "$repo/actions/runs/$run_id" > "$dest/run.json"
gh api "$repo/actions/runs/$run_id/artifacts" > "$dest/artifacts.json"
artifact_id=$(jq -er '.artifacts | if length == 1 then .[0].id else error("expected one artifact") end' "$dest/artifacts.json")
gh api "$repo/actions/artifacts/$artifact_id/zip" > "$dest/artifact.zip"
gh api "$repo/actions/runs/$run_id/attempts/1/logs" > "$dest/logs.zip"
python3 - "$dest" <<'PY'
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

root = Path(sys.argv[1])
def require(condition, message):
    if not condition:
        raise ValueError(message)
def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()
def data(path):
    return json.loads(path.read_text())
def inventory(source):
    with source as archive:
        return {entry.name: hashlib.file_digest(archive.extractfile(entry), 'sha256').hexdigest()
                for entry in archive if entry.isfile()}

artifact, = data(root / 'artifacts.json')['artifacts']
run = data(root / 'run.json')
require(run['conclusion'] == 'success', 'native diagnostic workflow failed')
zip_file = root / 'artifact.zip'
zip_sha256 = digest(zip_file)
require('sha256:' + zip_sha256 == artifact['digest'], 'artifact digest mismatch')
require(zip_file.stat().st_size == artifact['size_in_bytes'], 'artifact size mismatch')
with zipfile.ZipFile(zip_file) as archive, zipfile.ZipFile(root / 'logs.zip') as logs:
    require(archive.testzip() is None and logs.testzip() is None, 'ZIP CRC failed')
    members = archive.namelist()
    require(len(set(members)) == len(members) and all(
        not Path(name).is_absolute() and '..' not in Path(name).parts for name in members),
        'unsafe archive path')
    archive.extractall(root / 'artifact')
directory = root / 'artifact'
build = data(directory / 'build-metadata.json')
require(run['head_sha'] == build['commit'], 'source revision mismatch')
require('x86_64' in build['host'] and ('Ubuntu' in build['host'] or 'Ubuntu' in build['os_release']),
        'expected native x86-64 Ubuntu')
source = directory / 'source.tar.gz'
require(digest(source) == build['source_archive_sha256'], 'source archive digest mismatch')
files = inventory(tarfile.open(source))
with subprocess.Popen(['git', 'archive', run['head_sha']], stdout=subprocess.PIPE) as checkout:
    local_files = inventory(tarfile.open(fileobj=checkout.stdout, mode='r|'))
    require(checkout.wait() == 0, 'git archive failed')
require(files == build['source_sha256'] == local_files, 'source file inventory mismatch')
binary_sha256 = digest(directory / 'oximut')
require(binary_sha256 == build['binary_sha256'], 'binary mismatch')
require(run['event'] == 'workflow_dispatch', 'unexpected workflow event')
result = {
    'run_url': run['html_url'], 'commit': run['head_sha'],
    'artifact_sha256': zip_sha256, 'log_sha256': digest(root / 'logs.zip'),
    'source_files': len(files), 'binary_sha256': binary_sha256,
    'build': {k: v for k, v in build.items() if k != 'source_sha256'},
}
(root / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
PY
