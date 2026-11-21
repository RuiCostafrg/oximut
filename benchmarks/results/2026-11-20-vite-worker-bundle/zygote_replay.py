"""Summarize zygote-launch-replay.json: fresh restored workers versus zygote forks.

Values are per-fork medians in milliseconds over measured waves (round >= 0).
Child CPU is process.cpuUsage() at Vitest's `started` reply. A forked child's
rusage restarts at fork(), so the zygote's own CPU, which includes page-table
copying, is reported separately from /proc clock ticks per fork.
"""
import json
import statistics
import sys
from pathlib import Path


def summarize(path):
    data = json.loads(Path(path).read_text())
    assert data['node'] == 'v26.3.1' and data['width'] == 4
    assert data['zygote']['exit'] == {'code': 0, 'signal': None}, data['zygote']
    records = [r for r in data['records'] if r['round'] >= 0]
    pids = [r['pid'] for r in data['records']]
    assert len(set(pids)) == len(pids) and data['zygote']['pid'] not in pids
    out = {'node': data['node'], 'v8': data['v8'], 'arch': data['arch'], 'rounds': data['rounds'],
           'exec_argv': data['execArgv'], 'arms': {}}
    cpu = lambda usage: (usage['user'] + usage['system']) / 1000
    for kind in ('fresh', 'zygote'):
        rows = [r for r in records if r['kind'] == kind]
        assert len(rows) == data['rounds'] * data['width']
        assert all(r['code'] == 0 and r['signal'] is None or r['code'] is None and r['signal'] == 'SIGTERM'
                   for r in rows)
        metric = lambda f: statistics.median(f(r) for r in rows)
        out['arms'][kind] = {
            'forks': len(rows),
            'wall_fork_to_started_ms': metric(lambda r: (int(r['start']['receiptNs']) - int(r['forkNs'])) / 1e6),
            'wall_fork_to_exit_ms': metric(lambda r: (int(r['exitNs']) - int(r['forkNs'])) / 1e6),
            'child_cpu_at_started_ms': metric(lambda r: cpu(r['start']['response']['cpuUs'])),
            'child_user_cpu_at_started_ms': metric(lambda r: r['start']['response']['cpuUs']['user'] / 1000),
            'child_system_cpu_at_started_ms': metric(lambda r: r['start']['response']['cpuUs']['system'] / 1000),
            'start_request_to_started_ms': metric(lambda r: (int(r['start']['receiptNs']) - int(r['start']['sendNs'])) / 1e6),
        }
    waves = [w for w in data['zygoteWaves'] if w['round'] >= 0]
    tick_ms = 1000 / data['clockTicksPerSecond']
    zygote_ms = sum(w['zygoteTicks'] for w in waves) * tick_ms / sum(w['forks'] for w in waves)
    out['arms']['zygote']['zygote_cpu_per_fork_ms'] = zygote_ms
    out['arms']['zygote']['total_cpu_per_fork_ms'] = out['arms']['zygote']['child_cpu_at_started_ms'] + zygote_ms
    boot = data['zygote']['bootNs']
    out['zygote_boot_wall_ms'] = (int(boot[1]) - int(boot[0])) / 1e6
    return out


if __name__ == '__main__':
    print(json.dumps(summarize(sys.argv[1]), indent=2))
