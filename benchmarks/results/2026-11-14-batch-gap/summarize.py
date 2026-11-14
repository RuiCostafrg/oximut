"""Attribute native reply-to-next-send intervals in an instrumented main capture.

Apply attribution.patch to 7297840; set OXIMUT_BATCH_GAP_TRACE to a directory.
These are overhead-bearing diagnostics, not scored campaign measurements.
"""
import gzip
import json
from pathlib import Path
import statistics
import sys


def summarize(root, skip=()):
    traces = [[json.loads(line) for line in (
        gzip.decompress(path.read_bytes()).decode() if path.suffix == '.gz'
        else path.read_text()).splitlines()]
        for path in sorted(root.glob('*.jsonl*'))]
    all_events = sorted((e for events in traces for e in events), key=lambda e: e['ns'])
    rows = []
    for events in traces:
        previous = None
        for e in events:
            if e['event'] == 'controller.reply':
                previous = e['ns']
            elif e['event'] == 'controller.send' and previous is not None:
                start, end = previous, e['ns']
                row = {'gap': (end - start) / 1e6}
                for stage in ['verification', 'fingerprint', 'sample', 'response.read',
                              'atomic.file-sync', 'atomic.dir-sync']:
                    spans = []
                    for trace in traces:
                        begin = None
                        for event in trace:
                            if event['event'] == stage + '.begin':
                                begin = event['ns']
                            elif event['event'] == stage + '.end' and begin is not None:
                                if event['ns'] > start and begin < end:
                                    spans.append((max(start, begin), min(end, event['ns'])))
                                begin = None
                    # Union: a scheduler sample may overlap a worker's sync.
                    merged = []
                    for a, b in sorted(spans):
                        if merged and a <= merged[-1][1]:
                            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                        else:
                            merged.append((a, b))
                    row[stage] = sum(b - a for a, b in merged) / 1e6
                # Exclusive path boundaries, including any uninstrumented work/waits.
                within = [x for x in all_events if start <= x['ns'] <= end]
                for a, b, label in [
                    ('controller.reply', 'verification.begin', 'result_envelope'),
                    ('verification.end', 'coordinator.return', 'post_verify'),
                    ('coordinator.return', 'response.read.begin', 'response_handoff'),
                    ('response.read.end', 'batch.assembly.begin', 'completion_sampling'),
                    ('batch.assembly.begin', 'batch.assembled', 'batch_assembly_and_sampling'),
                    ('batch.assembled', 'worker.request.read', 'request_handoff'),
                    ('worker.request.read', 'fingerprint.begin', 'request_parsing'),
                    ('fingerprint.end', 'controller.send', 'request_preparation'),
                ]:
                    aa = next((x['ns'] for x in within if x['event'] == a), None)
                    bb = next((x['ns'] for x in within if x['event'] == b), None)
                    row[label] = (bb-aa)/1e6 if aa is not None and bb is not None else None
                rows.append(row)
                previous = None
    excluded = [r for i, r in enumerate(rows) if i in skip]
    rows = [r for i, r in enumerate(rows) if i not in skip]
    return {'intervals': len(rows), 'excluded': excluded, 'median_ms': {
        key: (statistics.median(values) if (values := [r[key] for r in rows if r[key] is not None]) else None)
        for key in rows[0]}, 'rows': rows}


if __name__ == '__main__':
    result = summarize(Path(sys.argv[1]), [int(i) for i in sys.argv[2:]])
    print(json.dumps(result, indent=2))
