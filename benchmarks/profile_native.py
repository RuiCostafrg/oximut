#!/usr/bin/env python3
"""One opt-in, overhead-bearing qualification capture; never a scored comparison."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import pstats
import subprocess
import sys
from types import SimpleNamespace

import real_project as workload

ROOT = workload.ROOT
SUPPORT = ROOT / 'benchmarks/profiling'


def qualification_errors(directory, code):
    """Require one complete equivalent workload group without published ratios."""
    errors = []
    if code != 0:
        errors.append(f'qualification harness exited {code}')
    try:
        summary = json.loads((directory / 'summary.json').read_text())
        rows = [json.loads(line) for line in (directory / 'measurements.jsonl').read_text().splitlines()]
        # Explicitly reject missing/duplicate groups; compare_rows skips absent groups.
        if len(rows) != 3 or sorted((r['round'], r['tool'], r['warmup']) for r in rows) != [
                (0, tool, False) for tool in ('oximut', 'stryker', 'vitest')]:
            errors.append('require exactly one complete three-tool qualification group')
        comparisons, _ = workload.compare_rows(rows)
        if (summary['correctness_gate'] is not True or len(comparisons) != 1
                or not comparisons[0]['ratio_eligible'] or summary['ratios']):
            errors.append('exact workload gate failed or ratios were published')
        for row in rows:
            if row['tool'] != 'vitest' and (len(row.get('mutations', {})) != 10 or any(
                    m['outcome'] != 'killed' for m in row['mutations'].values())):
                errors.append('require exactly ten killed edits per mutation tool')
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f'missing/malformed qualification evidence: {error}')
    return errors


def node_profile_finalized(data):
    """Check that parsed Node evidence has nodes and finite ordered timestamps."""
    try:
        return (isinstance(data, dict) and isinstance(data.get('nodes'), list) and bool(data['nodes'])
                and all(isinstance(node, dict) for node in data['nodes'])
                and all(type(data.get(key)) in (int, float) and math.isfinite(data[key])
                        for key in ('startTime', 'endTime'))
                and data['endTime'] >= data['startTime'] >= 0)
    except OverflowError:
        return False


def compact_summary(result):
    """Bound displayed diagnostics while retaining the full inventory in artifacts."""
    captures = result.get('captures', {})
    errors = [*result.get('qualification_errors', []), *captures.get('errors', [])]
    return {
        'diagnostic_only': True,
        'passed': result.get('passed', False),
        'node_capture_mode': result.get('node_capture_mode'),
        'failure': str(result['failure'])[:500] if result.get('failure') else None,
        'observed_identity_count': len(captures.get('observed', [])),
        'orphan_profile_count': len(captures.get('orphan_profiles', [])),
        'error_count': len(errors),
        'first_errors': [str(error)[:500] for error in errors[:5]],
        'full_evidence': 'capture/diagnostic.json and raw files in the uploaded artifact',
    }


def capture_inventory(directory, qualification=None):
    """Report observed captures only. Absence cannot establish process completeness."""
    records, errors, identities, matched = [], [], [], set()
    for identity in sorted(directory.glob('*.json')):
        try:
            record = json.loads(identity.read_text())
            segments = []
            partial = False
            if identity.name.startswith('node-'):
                profiles = sorted(directory.glob(f'CPU.*.{record["pid"]}.{record["threadId"]}.*.cpuprofile'))
                matched.update(profiles)
                segments = sorted(
                    directory.glob(f'Segment.{record["pid"]}.{record["threadId"]}.{record.get("startTime")}.*.cpuprofile'),
                    key=lambda path: int(path.name.rsplit('.', 2)[1]))
                matched.update(segments)
                partial = bool(segments)
                for path in segments:
                    partial &= node_profile_finalized(json.loads(path.read_text()))
                if segments and not partial:
                    errors.append(f'invalid partial segment: {identity.name}')
                complete = bool(profiles) and 'exitObserved' in record
                for path in profiles:
                    data = json.loads(path.read_text())
                    complete &= node_profile_finalized(data)
            else:
                profiles = [identity.with_suffix('.pstats')]
                matched.update(profiles)
                complete = record['completed'] and profiles[0].is_file()
                if complete:
                    pstats.Stats(str(profiles[0]))
            if (complete or partial) and not (
                    type(record.get('pid')) is int and record['pid'] > 0
                    and type(record.get('ppid')) is int and record['ppid'] > 0
                    and isinstance(record.get('cwd'), str) and Path(record['cwd']).is_absolute()
                    and isinstance(record.get('argv'), list) and bool(record['argv'])
                    and all(isinstance(arg, str) for arg in record['argv'])
                    and isinstance(record.get('startTime'), str)
                    and record['startTime'].isascii() and record['startTime'].isdecimal()):
                complete = False
                partial = False
                errors.append(f'invalid process identity: {identity.name}')
            if (complete or partial) and identity.name.startswith('node-') and (
                    type(record.get('threadId')) is not int or record['threadId'] < 0):
                complete = False
                partial = False
                errors.append(f'invalid Node thread identity: {identity.name}')
            records.append({'identity': identity.name, 'profiles': [p.name for p in profiles],
                            'partial_segment_count': len(segments),
                            'partial_segment_range': [segments[0].name, segments[-1].name] if segments else [],
                            'partial_segments_finalized': partial,
                            'exit_observed': 'exitObserved' in record if identity.name.startswith('node-') else None,
                            'capture_present_and_finalized': complete})
            if complete or partial:
                identities.append((identity.name, record))
            if not complete:
                errors.append(f'incomplete capture: {identity.name}')
        except Exception as error:
            errors.append(f'invalid capture {identity.name}: {error}')
    if not any(r['identity'].startswith('node-') for r in records):
        errors.append('no Node identities captured')
    if not any(r['identity'].startswith('python-') for r in records):
        errors.append('no Python supervisor identities captured')
    orphans = []
    for path in sorted((set(directory.glob('*.cpuprofile')) | set(directory.glob('*.pstats'))) - matched):
        try:
            if path.suffix == '.pstats':
                pstats.Stats(str(path))
                finalized = True
            else:
                finalized = node_profile_finalized(json.loads(path.read_text()))
        except Exception:
            finalized = False
        orphans.append({'profile': path.name, 'profile_finalized': finalized, 'identity_missing': True})
        errors.append(f'orphan profile lacks identity: {path.name}')
    roles = {}
    if qualification is not None:
        tool_records = {tool: [] for tool in ('vitest', 'oximut', 'stryker')}
        for name, record in identities:
            try:
                cwd = Path(record['cwd']).resolve()
                for tool in tool_records:
                    project = (qualification / f'00-{tool}' / 'project').resolve()
                    if cwd.is_relative_to(project):
                        tool_records[tool].append((name, record))
            except (KeyError, TypeError, ValueError):
                errors.append(f'invalid process cwd: {name}')
        for tool, values in tool_records.items():
            roles[f'{tool}_node'] = [name for name, _ in values if name.startswith('node-')]
            if not roles[f'{tool}_node']:
                errors.append(f'missing finalized Node capture for {tool}')
        oxi = tool_records['oximut']
        adapter = str(ROOT / 'packages/vitest-adapter')
        adapters = [(name, r) for name, r in oxi if name.startswith('node-')
                    and isinstance(r.get('argv'), list) and r['argv'][1:2] == [adapter + '/m2-runner.mjs']]
        adapter_pids = {r['pid'] for _, r in adapters}
        supervisors = [(name, r) for name, r in oxi if name.startswith('python-')
                       and r.get('ppid') in adapter_pids and isinstance(r.get('argv'), list)
                       and r['argv'][:1] == [adapter + '/supervisor.py']]
        supervisor_pids = {r['pid'] for _, r in supervisors}
        controllers = [(name, r) for name, r in oxi if name.startswith('node-')
                       and r.get('ppid') in supervisor_pids and isinstance(r.get('argv'), list)
                       and r['argv'][1:2] == [adapter + '/m2-worker.ts']]
        controller_pids = {r['pid'] for _, r in controllers}
        forks = [(name, r) for name, r in oxi if name.startswith('node-')
                 and r.get('ppid') in controller_pids and r.get('pid') not in controller_pids]
        for role, values in [('oximut_adapter', adapters), ('oximut_supervisor', supervisors),
                             ('oximut_controller', controllers), ('oximut_test_fork', forks)]:
            roles[role] = [name for name, _ in values]
            if not values:
                errors.append(f'missing finalized process-role capture: {role}')
    return {'observed': records, 'orphan_profiles': orphans, 'minimum_role_evidence': roles, 'errors': errors,
            'role_evidence_policy': 'Finalized full profiles or explicitly partial segments. Missing lifecycle exits/full profiles still fail capture validation.',
            'completeness': 'Unknown: processes killed before preload/dump and overridden environments may be absent; raw orphan profiles retained.'}


def main(argv=None):
    """Retain one diagnostic capture and fail on workload or capture errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--segments', action='store_true',
                        help='Use one inspector startup checkpoint per isolate instead of exit-time Node profiles; always partial evidence')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    profiles = output / 'profiles'
    profiles.mkdir()
    result = {'diagnostic_only': True, 'profiler_overhead': True,
              'node_capture_mode': 'partial-inspector-startup' if args.segments else 'exit-time-cpu-prof',
              'qualification_errors': [], 'failure': None,
              'scope': 'Unchanged colord: 41 sources, 102 tests, five mutation sources, ten arithmetic edits. One capture, no warmup campaign.',
              'limits': 'Node sampling and Python supervisor self CPU only; no complete process-tree CPU partition. Rust resource sampling remains unattributed. External benchmark sampler CPU is outside wait4 product-tree CPU; it perturbs wall time.'}
    try:
        if os.environ.get('NODE_OPTIONS'):
            raise ValueError('start with NODE_OPTIONS unset to keep profiler options explicit')
        adapter = ROOT / 'packages/vitest-adapter'
        inputs = SimpleNamespace(binary=args.binary.resolve(), adapter=adapter / 'm2-runner.mjs',
                                 adapter_source_dir=adapter)
        result['execution_inputs'] = workload.execution_inputs(inputs)
        result['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        result['helper_sha256'] = {str(p.relative_to(ROOT)): workload.measurement.file_hash(p)
                                   for p in [Path(__file__), *SUPPORT.glob('*')] if p.is_file()}
        shimdir = output / 'shim'
        shimdir.mkdir()
        shim = shimdir / 'python3'
        shim.write_text(f'#!{sys.executable}\n' + (SUPPORT / 'python-shim.py').read_text())
        shim.chmod(0o755)
        env = {**os.environ, 'PATH': str(shimdir) + os.pathsep + os.environ['PATH'],
               'OXIMUT_PROFILE_PYTHON': sys.executable,
               'OXIMUT_PROFILE_SUPERVISOR': str(adapter / 'supervisor.py'),
               'OXIMUT_PROFILE_DIR': str(profiles),
               'OXIMUT_PROFILE_SEGMENTS': '1' if args.segments else '0',
               'NODE_OPTIONS': ('' if args.segments else f'--cpu-prof --cpu-prof-dir="{profiles}" ') +
                               f'--require="{SUPPORT / "node-identity.cjs"}"'}
        result['node_options'] = env['NODE_OPTIONS']
        cmd = [sys.executable, str(ROOT / 'benchmarks/real_project.py'),
               '--binary', str(inputs.binary), '--adapter', str(inputs.adapter),
               '--adapter-source-dir', str(adapter), '--defer-ratios',
               '--tools', 'vitest', 'oximut', 'stryker', '--warmups', '0',
               '--repetitions', '1', '--timeout', '600', '--output', str(output / 'qualification')]
        result['command'] = cmd
        with (output / 'qualification.log').open('w') as log:
            code = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        result['qualification_errors'] = qualification_errors(output / 'qualification', code)
        if workload.execution_inputs(inputs) != result['execution_inputs']:
            result['qualification_errors'].append('binary/adapter changed during capture')
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
    finally:
        result['captures'] = capture_inventory(profiles, output / 'qualification')
        result['passed'] = not (result['failure'] or result['qualification_errors'] or result['captures']['errors'])
        workload.m6.write_json(output / 'diagnostic.json', result)
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
