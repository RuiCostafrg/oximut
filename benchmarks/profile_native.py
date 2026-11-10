#!/usr/bin/env python3
"""One opt-in, overhead-bearing qualification capture; never a scored comparison."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import pstats
import re
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
    """Require a connected CPU call tree and usable, internally consistent samples."""
    try:
        if (not isinstance(data, dict) or not isinstance(data.get('nodes'), list) or not data['nodes']
                or not all(type(data.get(k)) in (int, float) and math.isfinite(data[k])
                           for k in ('startTime', 'endTime'))
                or not data['endTime'] > data['startTime'] >= 0):
            return False
        nodes = {}
        for node in data['nodes']:
            if not isinstance(node, dict) or type(node.get('id')) is not int or node['id'] <= 0:
                return False
            frame = node.get('callFrame')
            if (node['id'] in nodes or not isinstance(frame, dict)
                    or not all(isinstance(frame.get(k), str) for k in ('functionName', 'scriptId', 'url'))
                    or not all(type(frame.get(k)) is int and frame[k] >= -1
                               for k in ('lineNumber', 'columnNumber'))
                    or not isinstance(node.get('children', []), list)
                    or not all(type(child) is int for child in node.get('children', []))):
                return False
            nodes[node['id']] = node
        children = [child for node in nodes.values() for child in node.get('children', [])]
        if (len(children) != len(set(children)) or any(child not in nodes for child in children)):
            return False
        roots = nodes.keys() - set(children)
        if len(roots) != 1 or data['nodes'][0]['id'] not in roots:
            return False
        visited, pending = set(), list(roots)
        while pending:
            node_id = pending.pop()
            if node_id in visited:
                return False
            visited.add(node_id)
            pending.extend(nodes[node_id].get('children', []))
        samples, deltas = data.get('samples'), data.get('timeDeltas')
        return (len(visited) == len(nodes)
                and isinstance(samples, list) and bool(samples) and isinstance(deltas, list)
                and len(samples) == len(deltas)
                and all(type(sample) is int and sample in nodes for sample in samples)
                and all(type(delta) in (int, float) and math.isfinite(delta) and delta >= 0
                        for delta in deltas)
                and math.fsum(deltas) <= data['endTime'] - data['startTime'])
    except (OverflowError, TypeError, ValueError):
        return False


def valid_mutant_identity(value):
    """Accept only the bounded lowercase digest emitted as an Oximut mutant ID."""
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def controller_capture_inventory(directory, qualification=None):
    """Validate the deliberately narrow controller capture, never whole-tree coverage."""
    errors, observed, matched = [], [], set()
    if list(directory.glob('controller-*.timeline-error')):
        errors.append('timeline append failure marker present')
    timelines = sorted(directory.glob('controller-*.jsonl'))
    for timeline in timelines:
        try:
            events = [json.loads(line) for line in timeline.read_text().splitlines()]
            if not events or any(not isinstance(e, dict) for e in events):
                raise ValueError('empty or malformed timeline')
            if type(events[0].get('formatVersion')) is not int or events[0]['formatVersion'] != 1:
                raise ValueError('unsupported legacy timeline: pre-send boundary/no append-integrity attestation')
            if any(type(e.get('seq')) is not int or e['seq'] != i
                   or e.get('timelineWriteFailed') is not False for i, e in enumerate(events, 1)):
                raise ValueError('missing event sequence or timeline append failure')
            times = [e['monotonicMs'] for e in events]
            if (not all(type(t) in (int, float) and math.isfinite(t) and t >= 0 for t in times)
                    or times != sorted(times)):
                raise ValueError('invalid monotonic timeline')
            if any(e['type'] == 'profile-error' for e in events):
                errors.append(f'diagnostic helper error: {timeline.name}')
            identities = [e for e in events if e['type'] == 'controller-start']
            exits = [e for e in events if e['type'] == 'controller-exit']
            if (len(identities) != 1 or len(exits) != 1
                    or events[0]['type'] != 'controller-start'
                    or events[-1]['type'] != 'controller-exit'
                    or type(identities[0].get('pid')) is not int
                    or identities[0]['pid'] <= 0
                    or identities[0]['pid'] != int(timeline.stem.split('-')[1])
                    or not str(identities[0].get('startTime', '')).isascii()
                    or not str(identities[0].get('startTime', '')).isdecimal()):
                errors.append(f'incomplete controller identity/lifecycle: {timeline.name}')
            if len(exits) == 1 and (type(exits[0].get('code')) is not int or exits[0]['code'] != 0):
                errors.append(f'abnormal controller exit: {timeline.name}')
            requests = [e for e in events if e['type'] == 'controller-request']
            replies = [e for e in events if e['type'] == 'controller-send']
            if (not requests or requests[0].get('command') != 'initialize'
                    or requests[-1].get('command') != 'close'
                    or any(e.get('command') != 'run' or e.get('kind') not in ('baseline', 'mutant')
                           for e in requests[1:-1])):
                errors.append(f'invalid controller command sequence: {timeline.name}')
            if any(e.get('boundary') != 'send-return' or type(e.get('sendReturn')) is not bool
                   for e in replies):
                errors.append(f'missing local send-return boundary: {timeline.name}')
            ids = [e['id'] for e in requests]
            reply_ids = [e['id'] for e in replies]
            if (not ids or any(type(i) is not int or abs(i) > 2**53 - 1 for i in ids + reply_ids)
                    or len(ids) != len(set(ids)) or len(reply_ids) != len(set(reply_ids))):
                errors.append(f'invalid or duplicate controller IDs: {timeline.name}')
            exchanges = [(e['type'], e['id']) for e in events
                         if e['type'] in ('controller-request', 'controller-send')]
            expected = [pair for i in ids for pair in
                        [('controller-request', i), ('controller-send', i)]]
            if exchanges != expected:
                errors.append(f'unmatched or nonserial controller exchange: {timeline.name}')
            if any(e.get('error') or not e.get('result') for e in replies):
                errors.append(f'controller error reply: {timeline.name}')
            mutants = [e for e in requests if e.get('kind') == 'mutant']
            # A shared-lane batch carries one mutant per lane; a batch must name all of them.
            if any('laneCount' in e and (type(e['laneCount']) is not int or e['laneCount'] < 1
                                         or not isinstance(e.get('laneMutantIds'), list)
                                         or len(e['laneMutantIds']) != e['laneCount']
                                         or e['laneMutantIds'][0] != e.get('mutantId'))
                   for e in mutants):
                errors.append(f'unidentified shared-lane batch: {timeline.name}')
            lane_ids = [[*(e['laneMutantIds'] if 'laneCount' in e and isinstance(e.get('laneMutantIds'), list)
                          else [e.get('mutantId')])] for e in mutants]
            starts = [e for e in events if e['type'] == 'profile-started']
            written = [e for e in events if e['type'] == 'profile-written']
            profile_window = None
            if (not mutants or len(starts) != 1 or len(written) != 1
                    or starts[0]['jobId'] != mutants[0]['id']
                    or written[0]['jobId'] != mutants[0]['id']):
                errors.append(f'first mutant profile missing/duplicated: {timeline.name}')
            else:
                name = written[0]['profile']
                if not isinstance(name, str) or Path(name).name != name or not name.endswith('.cpuprofile'):
                    raise ValueError('unsafe profile filename')
                profile = directory / name
                matched.add(profile)
                if not node_profile_finalized(json.loads(profile.read_text())):
                    errors.append(f'invalid CPU profile: {name}')
                reply = next(e for e in replies if e['id'] == mutants[0]['id'])
                if not (mutants[0]['monotonicMs'] <= starts[0]['monotonicMs']
                        <= reply['monotonicMs'] <= written[0]['monotonicMs']):
                    errors.append(f'CPU sample window outside mutant timeline: {timeline.name}')
                else:
                    profile_window = {
                        'requestToSendReturnMs': reply['monotonicMs'] - mutants[0]['monotonicMs'],
                        'requestToProfileAckMs': starts[0]['monotonicMs'] - mutants[0]['monotonicMs'],
                        'profileAckToSendReturnMs': reply['monotonicMs'] - starts[0]['monotonicMs'],
                    }
                forks = [e for e in events if e['type'] == 'fork'
                         and starts[0]['monotonicMs'] <= e['monotonicMs'] <= reply['monotonicMs']]
                if not forks:
                    errors.append(f'no first-mutant fork evidence: {timeline.name}')
            forks = [e['childPid'] for e in events if e['type'] == 'fork']
            exited = [e['childPid'] for e in events if e['type'] == 'child-exit']
            if not forks or sorted(forks) != sorted(exited):
                errors.append(f'incomplete observed worker exits: {timeline.name}')
            for pid in forks:
                boundaries = []
                for event in events:
                    if event.get('childPid') != pid:
                        continue
                    if event['type'] in ('fork', 'child-exit'):
                        boundaries.append(event['type'])
                    elif event['type'] == 'child-send':
                        boundaries.append('send:' + str(event.get('messageType')))
                    elif event['type'] == 'child-message':
                        if event.get('messageType'):
                            boundaries.append('receive:' + str(event['messageType']))
                        elif event.get('rpcMethod') == 'onCollected':
                            boundaries.append('collection')
                if boundaries != ['fork', 'send:start', 'receive:started', 'send:run',
                                   'collection', 'receive:testfileFinished', 'send:stop',
                                   'receive:stopped', 'child-exit']:
                    errors.append(f'incomplete worker boundaries: {timeline.name}/{pid}')
                worker_exit = next(e for e in events if e['type'] == 'child-exit'
                                   and e.get('childPid') == pid)
                # Vitest terminates isolated forks with SIGTERM after 'stopped'.
                clean_exit = ('signal' in worker_exit
                              and type(worker_exit.get('code')) is int and worker_exit['code'] == 0
                              and worker_exit.get('signal') is None)
                stopped_term = ('code' in worker_exit and worker_exit['code'] is None
                                and worker_exit.get('signal') == 'SIGTERM'
                                and 'receive:stopped' in boundaries)
                if not (clean_exit or stopped_term):
                    errors.append(f'abnormal observed worker exit: {timeline.name}/{pid}')
                counts = worker_exit.get('counts')
                critical = ('started', 'testfileFinished', 'stopped', 'onCollected')
                if (not isinstance(counts, dict) or any(type(counts.get(k)) is not int
                                                       or counts[k] != 1 for k in critical)):
                    errors.append(f'invalid worker boundary counts: {timeline.name}/{pid}')
            observed.append({'timeline': timeline.name, 'request_count': len(requests),
                             'mutant_count': sum(map(len, lane_ids)), 'observed_forks': len(forks),
                             'batch_sizes': [len(ids) for ids in lane_ids], 'mutant_request_count': len(mutants),
                             'mutant_ids': [i for ids in lane_ids for i in ids],
                             'baseline_count': sum(e.get('kind') == 'baseline' for e in requests),
                             'jobs': [{'kind': e.get('kind'), 'job_id': e.get('jobId'),
                                       'run_id': e.get('runId'), 'mutant_id': e.get('mutantId')}
                                      for e in requests if e.get('command') == 'run' and 'laneCount' not in e] +
                                     [{'kind': 'mutant', 'job_id': identity, 'run_id': e.get('runId'),
                                       'mutant_id': identity}
                                      for e in requests if e.get('command') == 'run' and 'laneCount' in e
                                      for identity in (e['laneMutantIds'] if isinstance(e.get('laneMutantIds'), list) else [])],
                             'first_mutant_window': profile_window})
        except (OSError, ValueError, KeyError, TypeError, StopIteration, OverflowError) as error:
            errors.append(f'invalid controller capture {timeline.name}: {error}')
    orphans = sorted(p.name for p in set(directory.glob('*.cpuprofile')) - matched)
    if orphans:
        errors.append('orphan controller profiles: ' + ', '.join(orphans))
    if not observed:
        errors.append('no usable controller timelines')
    reconciled = False
    try:
        if qualification is None:
            raise ValueError('qualification report required for workload completeness')
        report = json.loads((qualification / '00-oximut/report.json').read_text())
        mutants = report['mutants']
        baselines = report['resources']['disabledValidationRuns']
        if (not isinstance(mutants, list) or not mutants or not isinstance(baselines, list)
                or any(not isinstance(run, dict) or run.get('run') != 'instrumentedBaseline'
                       or type(run.get('coverage')) is not bool
                       for run in baselines)
                or any(m['provenance']['source'] != 'executed' for m in mutants)):
            raise ValueError('require executed-mutant and validation-run evidence')
        # Complete attribution is required by qualification. If none of the
        # mutation groups collected it, the CLI ran a separate coverage baseline,
        # which disabledValidationRuns intentionally does not include.
        baseline = report['baseline']
        if not isinstance(baseline, dict):
            raise ValueError('invalid qualified baseline evidence')
        coverage = baseline.get('coverage')
        coverage = {} if coverage is None else coverage
        if not isinstance(coverage, dict) or type(coverage.get('complete', False)) is not bool:
            raise ValueError('invalid qualified coverage evidence')
        coverage_only = (coverage.get('complete') is True
                         and not any(run['coverage'] for run in baselines))
        instrumented_count = len(baselines) + int(coverage_only)
        expected = {'mutant_count': len(mutants), 'baseline_count': 1 + instrumented_count}
        actual = {key: sum(r[key] for r in observed) for key in expected}
        if actual != expected or (sum(r['request_count'] for r in observed) !=
                              expected['baseline_count'] + sum(r['mutant_request_count'] for r in observed) + 2 * len(observed)):
            raise ValueError(f'captured workload counts {actual} differ from qualification {expected}')
        expected_ids = [m['id'] for m in mutants]
        captured_ids = [identity for row in observed for identity in row['mutant_ids']]
        if (not all(valid_mutant_identity(identity) for identity in expected_ids)
                or len(expected_ids) != len(set(expected_ids))):
            raise ValueError('invalid or duplicate qualified mutant identities')
        if (not all(valid_mutant_identity(identity) for identity in captured_ids)
                or sorted(captured_ids) != sorted(expected_ids)):
            raise ValueError('captured mutant identities differ from qualification')
        run_id = report['runId']
        if (not isinstance(run_id, str) or len(run_id) > 128
                or re.fullmatch(r'[0-9]+-[0-9]+', run_id) is None
                or baseline['jobId'] != 'baseline' or baseline['runId'] != run_id):
            raise ValueError('invalid qualified original baseline identity')
        jobs = [job for row in observed for job in row['jobs']]
        if any(job['run_id'] != run_id for job in jobs):
            raise ValueError('captured jobs belong to a different qualification run')
        # CLI original job ID plus its single or indexed instrumented-baseline IDs.
        expected_baselines = ['baseline'] + (
            ['instrumented-baseline'] if instrumented_count == 1 else
            [f'instrumented-baseline-{i}' for i in range(instrumented_count)])
        baseline_ids = [job['job_id'] for job in jobs if job['kind'] == 'baseline']
        if (not all(isinstance(identity, str) for identity in baseline_ids)
                or sorted(baseline_ids) != sorted(expected_baselines)):
            raise ValueError('captured baseline identities differ from qualification')
        if any(job['job_id'] != job['mutant_id'] for job in jobs if job['kind'] == 'mutant'):
            raise ValueError('captured mutant job identity differs from its mutation')
        reconciled = True
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f'workload reconciliation failed: {error}')
    return {'observed': observed, 'errors': errors, 'orphan_profiles': orphans,
            'workload_reconciled': reconciled,
            'scope': 'Request observation to local send return (not recipient delivery/callback completion); partial controller CPU sampling. '
                     'Sampling starts during profiler activation, omitting some request prefix, '
                     'and may include inspector overhead. Not full-request or process-tree CPU profiling.'}


def compact_summary(result):
    """Bound displayed diagnostics while retaining the full inventory in artifacts."""
    captures = result.get('captures', {})
    errors = [*result.get('qualification_errors', []), *captures.get('errors', [])]
    return {
        'diagnostic_only': True,
        'passed': result.get('passed', False),
        'node_capture_mode': result.get('node_capture_mode'),
        'failure': str(result['failure'])[:500] if result.get('failure') else None,
        'observed_identity_count': (None if result.get('node_capture_mode') == 'rust-accounting-self'
                                    else len(captures.get('observed', []))),
        'accounting_diagnostics': captures.get('accounting_diagnostics'),
        'accounting_clock_version': captures.get('clock_version'),
        'peak_memory_process_counts': {row['tool']:len(row['processes'])
                                       for row in captures.get('peak_rss_snapshots',[])},
        'orphan_profile_count': len(captures.get('orphan_profiles', [])),
        'error_count': len(errors),
        'first_errors': [str(error)[:500] for error in errors[:5]],
        'full_evidence': 'capture/diagnostic.json and raw files in the uploaded artifact',
    }


def accounting_capture_inventory(qualification, *, allow_legacy=False):
    """Reject incomplete self-accounting, without attributing worker/tree CPU."""
    errors, diagnostics, snapshots = [], None, []
    try:
        report = json.loads((qualification / '00-oximut' / 'report.json').read_text())
        resources = report['resources']
        diagnostics = resources['accountingDiagnostics']
        old_fields = {'scope','evidence','sampling','discovery','identityReads','rssReads','timingNote'}
        precise = isinstance(diagnostics,dict) and type(diagnostics.get('clockVersion')) is int and diagnostics['clockVersion']==2
        expected_fields = old_fields | ({'clockVersion','threadCpuSinceBudgetStartNs','threadCpuFailures'} if precise else set())
        if (not isinstance(diagnostics, dict) or set(diagnostics) != expected_fields
                or not precise and not allow_legacy):
            raise ValueError('missing/invalid accounting object')
        if any(not isinstance(diagnostics[key], str) or not 0 < len(diagnostics[key]) <= 2048
               for key in ('scope', 'evidence', 'timingNote')):
            raise ValueError('invalid accounting scope/notes')
        samples = resources['samples']
        if type(samples) is not int or not 0 < samples <= 2**64-1:
            raise ValueError('invalid admitted sample count')
        for name in ('sampling', 'discovery', 'identityReads', 'rssReads'):
            stage = diagnostics[name]
            fields = {'calls','wallNs','selfUserNs','selfSystemNs','cpuFailures','timingFailures'}
            if precise:
                fields |= {'selfCpuNs','selfCpuFailures'}
            if not isinstance(stage, dict) or set(stage) != fields:
                raise ValueError(f'invalid accounting stage: {name}')
            if any(type(value) is not int or not 0 <= value <= 2**64-1 for value in stage.values()):
                raise ValueError(f'incomplete/invalid accounting totals: {name}')
            if stage['calls'] == 0 or stage['cpuFailures'] != 0 or stage['timingFailures'] != 0:
                raise ValueError(f'empty or failed accounting stage: {name}')
            if precise and (stage['selfCpuFailures'] != 0 or stage['selfCpuNs'] > stage['wallNs']):
                raise ValueError(f'invalid precise CPU interval: {name}')
        if precise:
            total = diagnostics['threadCpuSinceBudgetStartNs']
            failures = diagnostics['threadCpuFailures']
            if (type(total) is not int or not 0 <= total <= 2**64-1
                    or type(failures) is not int or failures != 0
                    or total < diagnostics['sampling']['selfCpuNs']
                    or sum(diagnostics[name]['selfCpuNs'] for name in ('discovery','identityReads','rssReads')) > diagnostics['sampling']['selfCpuNs']):
                raise ValueError('invalid Budget thread CPU interval')
        if (diagnostics['sampling']['calls'] != samples
                or diagnostics['discovery']['calls'] != samples
                or diagnostics['rssReads']['calls'] > diagnostics['identityReads']['calls']):
            raise ValueError('accounting counts differ from resource observations')
        if resources['exhaustion'] is not None or not native_coordinator_evidence(qualification):
            raise ValueError('require healthy native resource accounting')
        rows = [json.loads(line) for line in (qualification/'measurements.jsonl').read_text().splitlines()]
        if sorted(row['tool'] for row in rows) != ['oximut','stryker','vitest']:
            raise ValueError('require three-tool peak memory evidence')
        for row in rows:
            if (type(row['peak_process_count']) is not int or row['peak_process_count'] <= 0
                    or type(row['observed_process_count']) is not int or row['observed_process_count'] < row['peak_process_count']
                    or type(row['peak_aggregate_rss_bytes']) is not int or not 0 < row['peak_aggregate_rss_bytes'] <= 2**64-1
                    or type(row['wall_seconds']) not in (int,float) or not math.isfinite(row['wall_seconds']) or row['wall_seconds'] <= 0):
                raise ValueError('invalid peak resource metrics')
            snapshot = row['peak_rss_snapshot']
            if (not isinstance(snapshot,dict) or set(snapshot) != {'root_pid','sampled_wall_seconds','processes'}
                    or type(snapshot.get('root_pid')) is not int or snapshot['root_pid'] <= 0
                    or type(snapshot.get('sampled_wall_seconds')) not in (int,float)
                    or not 0 <= snapshot['sampled_wall_seconds'] <= row['wall_seconds']):
                raise ValueError('invalid memory peak snapshot')
            processes = snapshot['processes']
            if not isinstance(processes,list) or not 0 < len(processes) <= min(4096,row['peak_process_count']):
                raise ValueError('incomplete memory peak process inventory')
            ids = set()
            for process in processes:
                if (not isinstance(process,dict) or set(process) != {'pid','ppid','starttime','rss_bytes','comm'}
                        or any(type(process[k]) is not int or not 0 <= process[k] <= 2**64-1 for k in ('pid','ppid','starttime','rss_bytes'))
                        or not isinstance(process['comm'],str) or len(process['comm']) > 64
                        or process['pid'] <= 0 or process['starttime'] <= 0 or process['pid'] in ids):
                    raise ValueError('invalid/duplicate peak process identity')
                ids.add(process['pid'])
            by_pid = {process['pid']:process for process in processes}
            if snapshot['root_pid'] not in by_pid:
                raise ValueError('peak snapshot lacks its root identity')
            for process in processes:
                chain = set()
                while process['pid'] != snapshot['root_pid']:
                    if process['pid'] in chain or process['ppid'] not in by_pid:
                        raise ValueError('peak process ancestry does not reach the captured root')
                    chain.add(process['pid'])
                    parent = by_pid[process['ppid']]
                    if parent['starttime'] > process['starttime']:
                        raise ValueError('peak parent identity starts after its child')
                    process = parent
            if sum(process['rss_bytes'] for process in processes) != row['peak_aggregate_rss_bytes']:
                raise ValueError('memory peak process sum differs from aggregate RSS')
            snapshots.append({'tool':row['tool'], **snapshot})
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f'accounting capture validation failed: {error}')
    return {'observed': [], 'orphan_profiles': [], 'errors': errors,
            'clock_version': 2 if isinstance(diagnostics,dict) and diagnostics.get('clockVersion')==2 else 1,
            'accounting_diagnostics': diagnostics,
            'peak_rss_snapshots': snapshots,
            'scope': 'CLI sampling-thread self CPU only; four nested stage totals, not a tree CPU partition. '
                     'Diagnostic clocks add overhead. No Node/Python sampling enabled in this mode.'}


# node-segments.cjs captures one checkpoint per isolate; events cannot raise this.
SEGMENTS_PER_ISOLATE = 1


def segment_events_match(events_file, segments):
    """Each helper event names one retained profile, with its times, within the helper's limit."""
    try:
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        profiles = {path.name: json.loads(path.read_text()) for path in segments}
    except (OSError, ValueError):
        return False
    if (len(events) != len(profiles) or len(profiles) > SEGMENTS_PER_ISOLATE
            or not all(isinstance(event, dict) for event in events)):
        return False
    named = [event.get('profile') for event in events]
    if sorted(named, key=str) != sorted(profiles) or any(
            event.get('maxSegmentsPerIsolate') != SEGMENTS_PER_ISOLATE
            or event.get('startTime') != profiles[event['profile']].get('startTime')
            or event.get('endTime') != profiles[event['profile']].get('endTime')
            for event in events):
        return False
    return True


def native_coordinator_evidence(qualification):
    """True only when the Oximut report exists, is valid JSON, and carries the native coordinator diagnostic."""
    try:
        report = json.loads((qualification / '00-oximut' / 'report.json').read_text())
        diagnostics = report['diagnostics']
        return isinstance(diagnostics, list) and any(
            isinstance(line, str) and line.startswith('native coordinator: ') for line in diagnostics)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def oximut_chain(identities, qualification):
    """Classify the Oximut process chain as 'legacy', 'native' or 'unclassified'.

    'legacy': a custom-adapter m2-runner.mjs Node identity is captured in the Oximut project, or there is no
    qualification root. A runner capture wins even when the report also claims the native coordinator: the
    stricter legacy role rules are the conservative choice when the evidence conflicts.
    'native': no runner capture AND the Oximut report positively states the native coordinator ran. An
    uncaptured legacy relay must not pass as native merely because it is absent from the captures.
    'unclassified': neither; the caller fails closed under the strict legacy rules."""
    if qualification is None:
        return 'legacy'
    project = (qualification / '00-oximut' / 'project').resolve()
    runner = [str(ROOT / 'packages/vitest-adapter/m2-runner.mjs')]
    for name, record in identities:
        try:
            if (name.startswith('node-') and isinstance(record.get('argv'), list)
                    and record['argv'][1:2] == runner and Path(record['cwd']).resolve().is_relative_to(project)):
                return 'legacy'
        except (KeyError, TypeError, ValueError):
            continue
    return 'native' if native_coordinator_evidence(qualification) else 'unclassified'


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
                # A finalized profile is not evidence unless its helper also completed:
                # the event record is written last, and failures leave an error file.
                stem = f'Segment.{record["pid"]}.{record["threadId"]}.{record.get("startTime")}'
                if (directory / f'{stem}.segment-error.txt').is_file():
                    partial = False
                    errors.append(f'segment helper failed: {identity.name}')
                elif partial and not (directory / f'{stem}.segment-events.jsonl').is_file():
                    partial = False
                    errors.append(f'segment events missing: {identity.name}')
                elif partial and not segment_events_match(directory / f'{stem}.segment-events.jsonl', segments):
                    partial = False
                    errors.append(f'segment events do not match profiles: {identity.name}')
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
            if record.get('initialIdentityWriteFailed'):
                complete = False
                partial = False
                errors.append(f'initial identity write failed: {identity.name}')
            if record.get('segmentCaptureError'):
                complete = False
                partial = False
                errors.append(f'segment helper failed: {identity.name}')
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
    # The legacy relay is identified by its captured Node runner; the native chain (the controller's parent is
    # the uncaptured Rust worker, no Python supervisor) additionally needs the report's native-coordinator
    # diagnostic. Anything else is unclassified and keeps the strict legacy requirements, failing closed.
    chain = oximut_chain(identities, qualification)
    if chain == 'unclassified':
        errors.append('oximut process chain unclassifiable: no native coordinator evidence and no legacy relay capture')
    if chain != 'native' and not any(r['identity'].startswith('python-') for r in records):
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
        worker = [adapter + '/m2-worker.ts']
        required = []
        if chain != 'native':
            adapters = [(name, r) for name, r in oxi if name.startswith('node-')
                        and isinstance(r.get('argv'), list) and r['argv'][1:2] == [adapter + '/m2-runner.mjs']]
            adapter_pids = {r['pid'] for _, r in adapters}
            supervisors = [(name, r) for name, r in oxi if name.startswith('python-')
                           and r.get('ppid') in adapter_pids and isinstance(r.get('argv'), list)
                           and r['argv'][:1] == [adapter + '/supervisor.py']]
            supervisor_pids = {r['pid'] for _, r in supervisors}
            controllers = [(name, r) for name, r in oxi if name.startswith('node-')
                           and r.get('ppid') in supervisor_pids and isinstance(r.get('argv'), list)
                           and r['argv'][1:2] == worker]
            required += [('oximut_adapter', adapters), ('oximut_supervisor', supervisors)]
        else:
            # The controller's parent is the Rust worker slot: no captured identity may own that pid.
            captured_pids = {r.get('pid') for _, r in identities}
            controllers = [(name, r) for name, r in oxi if name.startswith('node-')
                           and r.get('ppid') not in captured_pids and isinstance(r.get('argv'), list)
                           and r['argv'][1:2] == worker]
        controller_pids = {r['pid'] for _, r in controllers}
        forks = [(name, r) for name, r in oxi if name.startswith('node-')
                 and r.get('ppid') in controller_pids and r.get('pid') not in controller_pids]
        required += [('oximut_controller', controllers), ('oximut_test_fork', forks)]
        for role, values in required:
            roles[role] = [name for name, _ in values]
            if not values:
                errors.append(f'missing finalized process-role capture: {role}')
    return {'observed': records, 'orphan_profiles': orphans, 'minimum_role_evidence': roles, 'oximut_chain': chain, 'errors': errors,
            'role_evidence_policy': 'Finalized full profiles or explicitly partial segments. Missing lifecycle exits/full profiles still fail capture validation.',
            'completeness': 'Unknown: processes killed before preload/dump and overridden environments may be absent; raw orphan profiles retained.'}


def main(argv=None):
    """Retain one diagnostic capture and fail on workload or capture errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--segments', action='store_true',
                        help='Use one inspector startup checkpoint per isolate instead of exit-time Node profiles; always partial evidence')
    modes.add_argument('--controller-jobs', action='store_true',
                       help='Trace complete controller jobs with a partial first-mutant CPU sample window; no whole-tree CPU attribution')
    modes.add_argument('--accounting', action='store_true',
                       help='Measure Rust sampling-thread self CPU, without Node or Python profilers')
    parser.add_argument('--concurrency', type=workload.concurrency_arg, default=1,
                        help='Matched parallelism for Oximut --workers and Stryker concurrency (default 1)')
    args = parser.parse_args(argv)
    workload.require_matched_concurrency(args.concurrency)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    profiles = output / 'profiles'
    profiles.mkdir()
    result = {'diagnostic_only': True, 'profiler_overhead': True, 'concurrency': args.concurrency,
              'node_capture_mode': ('rust-accounting-self' if args.accounting else 'controller-jobs' if args.controller_jobs else
                                    'partial-inspector-startup' if args.segments else 'exit-time-cpu-prof'),
              'qualification_errors': [], 'failure': None,
              'scope': 'Unchanged colord: 41 sources, 102 tests, five mutation sources, ten arithmetic edits. One capture, no warmup campaign.',
              'limits': ('CLI sampling-thread self CPU, excluding worker CPU; clock overhead is included, no complete process-tree CPU partition.' if args.accounting else
                         'Node sampling and Python supervisor self CPU only; no complete process-tree CPU partition. Rust resource sampling remains unattributed.') +
                        ' External benchmark sampler CPU is outside wait4 product-tree CPU; it perturbs wall time.'}
    if args.accounting:
        result['accounting_clock_version'] = 2
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
        if args.controller_jobs:
            # Keep this capture separate from the startup/exit-time profilers:
            # two inspector sessions would otherwise stop each other's samples.
            env['OXIMUT_PROFILE_CONTROLLER_DIR'] = str(profiles)
            env['NODE_OPTIONS'] = f'--require="{SUPPORT / "controller-jobs.cjs"}"'
        if args.accounting:
            # No shim/preload/profiling variables: isolate the accountant cost
            # from inspector startup and Node/Python CPU sampling overhead.
            env = {**os.environ, 'NODE_OPTIONS': '', 'OXIMUT_PROFILE_ACCOUNTING': '1',
                   'OXIMUT_PROFILE_MEMORY': '1'}
        result['node_options'] = env['NODE_OPTIONS']
        cmd = [sys.executable, str(ROOT / 'benchmarks/real_project.py'),
               '--binary', str(inputs.binary), '--adapter', str(inputs.adapter),
               '--adapter-source-dir', str(adapter), '--defer-ratios',
               '--concurrency', str(args.concurrency),
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
        result['captures'] = (accounting_capture_inventory(output / 'qualification') if args.accounting else
                              controller_capture_inventory(profiles, output / 'qualification') if args.controller_jobs else
                              capture_inventory(profiles, output / 'qualification'))
        result['passed'] = not (result['failure'] or result['qualification_errors'] or result['captures']['errors'])
        workload.m6.write_json(output / 'diagnostic.json', result)
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
