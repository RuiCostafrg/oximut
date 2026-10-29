#!/usr/bin/env python3
"""Bind R3 lifecycle evidence to source bytes before source-only attestation."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def check(archive, evidence, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('full candidate revision required')
    data = json.loads(evidence.read_text())
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (data.get('gate') != 'R3' or data.get('status') != 'passed'
            or data.get('archiveSha256') != digest
            or data.get('source', {}).get('commit') != revision
            or data.get('version') != '1.0.0-rc.1'
            or data.get('isolation') != 'bubblewrap-pid-mount-v1'
            or data.get('buildCount') != 1
            or data.get('extractedSourceRemoved') is not True
            or not re.fullmatch(r'[0-9a-f]{64}', data.get('binarySha256', ''))):
        raise ValueError('successful R3 evidence does not match the candidate')
    tests = data.get('tests', [])
    if (len(tests) != 2 or sorted(t.get('phase', '') for t in tests) != ['installed', 'relocated']
            or any(t.get('passed') is not True for t in tests)):
        raise ValueError('both exact installation and relocation must pass')
    return digest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    try:
        print(check(args.archive, args.evidence, args.revision))
    except (ValueError, TypeError, AttributeError, OSError) as error:
        parser.exit(1, f'evidence check failed: {error}\n')
