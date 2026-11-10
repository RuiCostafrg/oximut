#!/usr/bin/env python3
"""Explain where Oximut's wall time, CPU and memory go, from retained native evidence.

Two stages, so the committed report can be regenerated after artifacts expire:

  extract  native artifact directories -> data.json (derived numbers + input hashes)
  render   data.json -> index.html, timeline.html, cpu.html (offline, no scripts needed)

Inputs (downloaded with `gh run download <id>` into new empty directories):
  --benchmark   real-project before/after campaign (paired ratios, phases)
  --accounting  native-diagnostic capture, mode accounting (Rust sampler self CPU)
  --controller  native-diagnostic capture, mode controller-jobs (job/fork timeline)
  --profiles    native-diagnostic capture, mode cpu-prof-exec-argv (Node CPU profiles)

Diagnostic captures are single overhead-bearing runs on possibly different runner
hosts; the report compares shares and ratios within one capture, never subtracts
absolute times across captures, and never presents them as scored ratios.
"""
import argparse
import collections
import glob
import hashlib
import html
import json
import os
import re
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import targets  # noqa: E402  (deterministic paired bootstrap used by the campaign)


def consume(path, inputs):
    """Hash every file whose contents reach data.json; return its text."""
    data = Path(path).read_bytes()
    inputs[str(path)] = hashlib.sha256(data).hexdigest()
    return data.decode('utf-8')


def read_json(path, inputs):
    return json.loads(consume(path, inputs))


def read_jsonl(path, inputs):
    return [json.loads(line) for line in consume(path, inputs).splitlines() if line.strip()]


def write_atomic(path, text):
    """Replace path only with a complete file, so readers never see a partial write."""
    path = Path(path)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False,
                                     prefix=f'.{path.name}.') as handle:
        handle.write(text)
    # NamedTemporaryFile creates 0600; give the report the usual umask-derived mode.
    mask = os.umask(0)
    os.umask(mask)
    os.chmod(handle.name, 0o666 & ~mask)
    os.replace(handle.name, path)


def one(root, pattern):
    # Shallowest match first: campaign artifacts nest per-round files of the same name.
    found = sorted(glob.glob(os.path.join(root, '**', pattern), recursive=True),
                   key=lambda path: (path.count(os.sep), path))
    if not found:
        raise SystemExit(f'missing {pattern} under {root}')
    return found[0]


def medians(rows, metrics):
    return {m: statistics.median(r[m] for r in rows) for m in metrics}


METRICS = ['wall_seconds', 'cpu_seconds', 'cpu_user_seconds', 'cpu_system_seconds',
           'peak_aggregate_rss_bytes', 'peak_process_count', 'observed_process_count']


COMPARISONS = {'before_vs_stryker': (('before', 'oximut'), ('before', 'stryker')),
               'after_vs_stryker': (('after', 'oximut'), ('after', 'stryker')),
               'after_vs_before': (('after', 'oximut'), ('before', 'oximut'))}


def validate_campaign(rows, summary):
    """Require one row per round/variant/tool and ratios recomputable from the raw rows."""
    if not (summary['correctness_gate']['passed'] and summary['build_integrity']['passed']):
        raise ValueError('campaign gates did not pass')
    warmups, pairs = summary['warmup_pairs'], summary['measured_pairs']
    indexed = {}
    for row in rows:
        key = (row.get('campaign_round'), row.get('variant'), row.get('tool'))
        if key in indexed:
            raise ValueError(f'duplicate campaign row {key}')
        indexed[key] = row
    expected = {(i, v, t) for i in range(warmups + pairs) for v in ('before', 'after')
                for t in ('vitest', 'oximut', 'stryker')}
    if set(indexed) != expected:
        raise ValueError('missing or unexpected campaign rows')
    for key, row in indexed.items():
        if row.get('warmup') != (key[0] < warmups):
            raise ValueError(f'inconsistent warmup label {key}')
    if len({row.get('workload_sha256') for row in rows}) != 1:
        raise ValueError('campaign rows declare different workloads')
    for name, ((nv, nt), (dv, dt)) in COMPARISONS.items():
        for metric, published in summary['comparisons'][name]['metrics'].items():
            value = targets.paired_interval([(indexed[(i, nv, nt)][metric], indexed[(i, dv, dt)][metric])
                                             for i in range(warmups, warmups + pairs)])
            if (abs(value['median'] - published['median']) > 1e-12 or
                    any(abs(a - b) > 1e-12 for a, b in zip(value['bootstrap_95_percent'],
                                                           published['bootstrap_95_percent'],
                                                           strict=True))):
                raise ValueError(f'{name} {metric}: summary disagrees with raw paired rows')
    return indexed


def extract_benchmark(root, inputs):
    summary = read_json(one(root, 'summary.json'), inputs)
    rows = read_jsonl(one(root, 'measurements.jsonl'), inputs)
    validate_campaign(rows, summary)
    tools = collections.defaultdict(list)
    for row in rows:
        if not row.get('warmup'):
            tools[f"{row['variant']}/{row['tool']}"].append(row)
    phases = collections.defaultdict(lambda: collections.defaultdict(list))
    for side, entries in summary['phase_evidence'].items():
        for entry in entries:
            if not entry.get('warmup'):
                for name, ms in entry['phaseWallMs'].items():
                    phases[side][name].append(ms)
    return {
        'gate': True,
        'workload_sha256': rows[0]['workload_sha256'],
        'pairs': summary['measured_pairs'],
        'medians': {k: medians(v, METRICS) for k, v in tools.items()},
        'ratios': {name: {m: {'median': x['median'], 'ci': x['bootstrap_95_percent']}
                          for m, x in cmp['metrics'].items()}
                   for name, cmp in summary['comparisons'].items()},
        'phases': {side: {k: statistics.median(v) for k, v in d.items()} for side, d in phases.items()},
    }


def diagnostic_identity(root, inputs):
    data = read_json(one(root, 'capture/diagnostic.json'), inputs)
    rows = read_jsonl(one(root, 'qualification/measurements.jsonl'), inputs)
    workloads = {r.get('workload_sha256') for r in rows}
    if len(workloads) != 1:
        raise ValueError(f'{root}: qualification rows declare different workloads')
    return data, rows, {'passed': data['passed'], 'commit': data.get('source_commit'),
                        'workload_sha256': workloads.pop(), 'mode': data.get('node_capture_mode')}


def extract_accounting(root, inputs):
    data, rows, identity = diagnostic_identity(root, inputs)
    captures = data['captures']
    return {
        'identity': identity,
        'passed': data['passed'],
        'sampler': captures['accounting_diagnostics'],
        'tools': {r['tool']: {m: r[m] for m in METRICS} for r in rows},
        'peak_rss_snapshots': captures['peak_rss_snapshots'],
    }


def extract_controller(root, inputs):
    _, rows, identity = diagnostic_identity(root, inputs)
    events = read_jsonl(one(root, 'controller-*.jsonl'), inputs)
    report = read_json(one(root, 'qualification/00-oximut/report.json'), inputs)
    vitest = next(r for r in rows if r['tool'] == 'vitest')
    jobs, forks, current = [], {}, None
    for e in events:
        kind, t = e['type'], e['monotonicMs']
        if kind == 'controller-request':
            current = {'command': e['command'], 'kind': e.get('kind'), 'mutant': e.get('mutantId'),
                       'lanes': e.get('laneCount'), 'start': t, 'cpu0': e['selfCpuUsage'], 'forks': []}
            jobs.append(current)
        elif kind == 'controller-send' and current is not None:
            current['end'], current['cpu1'] = t, e['selfCpuUsage']
        elif kind == 'fork':
            fork = {'pid': e['childPid'], 'fork': t, 'events': {}}
            forks[e['childPid']] = fork
            current['forks'].append(fork)
        elif kind in ('child-message', 'child-send', 'child-exit') and e.get('childPid') in forks:
            label = 'exit' if kind == 'child-exit' else (e.get('messageType') or e.get('rpcMethod'))
            forks[e['childPid']]['events'].setdefault(f'{kind}:{label}', t)
    out_jobs = []
    for job in jobs:
        segments = []
        for fork in job['forks']:
            ev = fork['events']
            started = ev['child-message:started']
            fetch = ev.get('child-message:fetch', started)
            collected = ev['child-message:onCollected']
            finished = ev['child-message:testfileFinished']
            exited = ev['child-exit:exit']
            segments.append({'pid': fork['pid'], 'fork': fork['fork'], 'started': started,
                             'fetch': fetch, 'collected': collected, 'finished': finished,
                             'exit': exited})
        cpu = (job['cpu1']['user'] - job['cpu0']['user'] + job['cpu1']['system'] - job['cpu0']['system']) / 1e3
        out_jobs.append({'command': job['command'], 'kind': job['kind'], 'mutant': job['mutant'],
                         'lanes': job['lanes'], 'start': job['start'], 'end': job['end'], 'controller_cpu_ms': cpu,
                         'forks': segments})
    exit_event = next(e for e in events if e['type'] == 'controller-exit')
    return {
        'identity': identity,
        'workload': {'tests': vitest['test_counts']['numTotalTests'],
                     'mutants': report['summary']['total'], 'killed': report['summary']['killed'],
                     'mutated_files': len(report['resources']['disabledValidationRuns'][0]['files'])},
        'controller_start_ms': events[0]['monotonicMs'],
        'controller_exit_ms': exit_event['monotonicMs'],
        'controller_self_cpu_ms': {k: v / 1e3 for k, v in exit_event['selfCpuUsage'].items()},
        'controller_rss_bytes_at_exit': exit_event['controllerMemoryBytes']['rss'],
        'phases': report['resources']['phaseWallMs'],
        'tools': {r['tool']: {m: r[m] for m in METRICS} for r in rows},
        'jobs': out_jobs,
    }


def category(frame):
    fn, url = frame['functionName'], frame['url']
    if fn == '(garbage collector)':
        return 'GC'
    if fn == '(idle)':
        return 'Idle (waiting)'
    if fn in ('(program)', '(root)'):
        return 'V8/native runtime'
    if url.startswith('node:internal/modules') or fn in ('compileSourceTextModule', 'compileFunction',
                                                         'wrapSafe', 'compileFunctionForCJSLoader'):
        return 'Node module loading/compile'
    if url.startswith('node:'):
        return 'Node bootstrap/runtime'
    match = re.search(r'node_modules/((?:@[^/]+/)?[^/]+)/', url)
    if match:
        pkg = match.group(1)
        if pkg.startswith('@vitest/') or pkg in ('vitest', 'tinypool', 'tinyspy', 'chai', 'tinyrainbow',
                                                  'expect-type', 'pathe'):
            return 'Vitest runtime (runner/expect/chai)'
        if pkg in ('vite', 'rollup', 'esbuild', 'rolldown', '@rolldown/pluginutils', 'es-module-lexer',
                   'magic-string', 'postcss', 'lightningcss', 'picomatch', 'fdir', 'tinyglobby'):
            return 'Vite transform/resolve'
        return 'Other dependencies'
    if '/packages/vitest-adapter/' in url:
        return 'Oximut adapter'
    if '/project/' in url:
        return 'colord source + tests'
    return 'V8/native runtime'


def extract_profiles(root, inputs):
    _, _, identity = diagnostic_identity(root, inputs)
    directory = os.path.dirname(one(root, 'node-*.json'))
    identities = {}
    for path in sorted(glob.glob(os.path.join(directory, 'node-*.json'))):
        ident = read_json(path, inputs)
        identities[(ident['pid'], ident['threadId'])] = ident
    controller = next(i for i in identities.values() if 'm2-worker' in ' '.join(i['argv']))
    roles = {'controller': [], 'fork': []}
    for path in sorted(glob.glob(os.path.join(directory, 'CPU.*.cpuprofile'))):
        pid, tid = map(int, os.path.basename(path).split('.')[3:5])
        ident = identities.get((pid, tid))
        if ident is None:
            continue
        argv = ' '.join(ident['argv'])
        if ident is controller:
            role = 'controller'
        elif 'workers/forks.js' in argv and ident['ppid'] == controller['pid']:
            role = 'fork'
        else:
            continue
        profile = read_json(path, inputs)
        nodes = {n['id']: n['callFrame'] for n in profile['nodes']}
        self_us = collections.Counter()
        for sample, delta in zip(profile['samples'], profile['timeDeltas']):
            self_us[sample] += delta
        categories, functions = collections.Counter(), collections.Counter()
        for node, us in self_us.items():
            frame = nodes[node]
            cat = category(frame)
            categories[cat] += us
            where = re.sub(r'.*node_modules/', '', frame['url']) or '(native)'
            functions[(cat, frame['functionName'] or '(anonymous)', where[-70:])] += us
        cpu = ident.get('selfCpuAtExitUs') or {}
        roles[role].append({'pid': pid, 'categories': dict(categories),
                            'functions': [[*k, v] for k, v in functions.most_common(80)],
                            'self_cpu_ms': {k: v / 1e3 for k, v in cpu.items()}})
    forks_expected = sum(1 for i in identities.values()
                         if 'workers/forks.js' in ' '.join(i['argv']) and i['ppid'] == controller['pid'])
    return {'identity': identity, 'roles': roles, 'forks_expected': forks_expected}


# ----------------------------------------------------------------------------- render

PALETTE_CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#f3f2ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;
--grid:#e4e3df;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--accent:#2a78d6}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--surface:#1a1a19;--panel:#242422;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#8f8e86;--grid:#34332f;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;
--s6:#008300;--accent:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:19px;margin:36px 0 8px}h3{font-size:16px;margin:20px 0 6px}p,li{color:var(--ink2)}
nav a{margin-right:14px;color:var(--accent);text-decoration:none;font-weight:600}nav{margin:10px 0 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:14px 0}
.tile{background:var(--panel);border-radius:10px;padding:12px 14px}.tile b{display:block;font-size:24px;color:var(--ink)}
.tile span{color:var(--ink2);font-size:13px}table{border-collapse:collapse;width:100%;margin:8px 0 4px;font-size:13.5px}
th,td{text-align:right;padding:5px 8px;border-bottom:1px solid var(--grid)}th:first-child,td:first-child{text-align:left}
th{color:var(--ink2);font-weight:600}details{margin:6px 0 14px}summary{cursor:pointer;color:var(--ink2);font-size:13px}
svg text{fill:var(--ink2);font-size:12px}svg .lbl{fill:var(--ink)}.note{font-size:13px;color:var(--muted)}
.rank{background:var(--panel);border-radius:10px;padding:12px 16px;margin:10px 0}.rank h3{margin:2px 0 4px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--ink2);margin:6px 0}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
code{font-size:12.5px}
"""

SLOTS = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)']


def esc(value):
    return html.escape(str(value))


def page(title, body, generated):
    nav = ('<nav><a href="index.html">Overview &amp; bottlenecks</a><a href="timeline.html">Job timeline</a>'
           '<a href="cpu.html">CPU &amp; memory</a></nav>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{PALETTE_CSS}</style>'
            f'</head><body><main><h1>{esc(title)}</h1><p class="note">{esc(generated)}</p>{nav}{body}</main></body></html>')


def legend(items):
    return '<div class="legend">' + ''.join(f'<span><i style="background:{c}"></i>{esc(n)}</span>' for n, c in items) + '</div>'


def table(headers, rows, caption):
    head = ''.join(f'<th>{esc(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in row) + '</tr>' for row in rows)
    return (f'<table><caption class="note">{esc(caption)}</caption><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table>')


def share(part, whole):
    return f'{100 * part / whole:.0f}%' if whole else 'n/a'


def stacked_bars(rows, unit, label, width=1000, bar=26, gap=14, label_w=170):
    """rows: [(label, [(segment_name, value, color)])]; horizontal stacked bars on one axis."""
    total_max = max(sum(v for _, v, _ in segs) for _, segs in rows) or 1
    plot = width - label_w - 70
    height = len(rows) * (bar + gap) + 30
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(label)}">']
    for tick in range(0, 6):
        x = label_w + plot * tick / 5
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 22}" stroke="var(--grid)"/>'
                   f'<text x="{x:.1f}" y="{height - 6}" text-anchor="middle">{total_max * tick / 5:.0f}{unit}</text>')
    for i, (row_label, segs) in enumerate(rows):
        y = i * (bar + gap) + 4
        out.append(f'<text class="lbl" x="{label_w - 10}" y="{y + bar / 2 + 4}" text-anchor="end">{esc(row_label)}</text>')
        x = label_w
        for name, value, color in segs:
            w = plot * value / total_max
            if w > 0:
                out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w - 2, 0.5):.1f}" height="{bar}" rx="4" '
                           f'fill="{color}"><title>{esc(row_label)} - {esc(name)}: {value:.0f}{unit}</title></rect>')
            x += w
        out.append(f'<text x="{x + 6:.1f}" y="{y + bar / 2 + 4}">{sum(v for _, v, _ in segs):.0f}{unit}</text>')
    out.append('</svg>')
    data = [(row_label, name, f'{value:.0f}{unit}') for row_label, segs in rows for name, value, _ in segs]
    out.append('<details><summary>Data table</summary>' +
               table(['Bar', 'Segment', 'Value'], data, f'Data for: {label}') + '</details>')
    return ''.join(out)


def ratio_chart(items, label, width=1000):
    """items: [(label, median, (lo, hi), target)] on one ratio axis with a 1.0 reference."""
    label_w, plot, row = 230, width - 300, 34
    lo_axis, hi_axis = 0.0, max(1.4, max(h for _, _, (_, h), _ in items) * 1.08)
    sx = lambda v: label_w + plot * (v - lo_axis) / (hi_axis - lo_axis)
    height = len(items) * row + 34
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(label)}">']
    for t in [0, 0.25, 0.5, 0.75, 1.0, 1.25]:
        if t <= hi_axis:
            out.append(f'<line x1="{sx(t):.1f}" y1="0" x2="{sx(t):.1f}" y2="{height - 22}" stroke="var(--grid)"/>'
                       f'<text x="{sx(t):.1f}" y="{height - 6}" text-anchor="middle">{t:.2f}</text>')
    out.append(f'<line x1="{sx(1):.1f}" y1="0" x2="{sx(1):.1f}" y2="{height - 22}" stroke="var(--ink2)" stroke-dasharray="4 3"/>')
    for i, (item_label, median, (lo, hi), target) in enumerate(items):
        y = i * row + 14
        out.append(f'<text class="lbl" x="{label_w - 10}" y="{y + 4}" text-anchor="end">{esc(item_label)}</text>')
        if target:
            out.append(f'<line x1="{sx(target):.1f}" y1="{y - 10}" x2="{sx(target):.1f}" y2="{y + 10}" stroke="var(--s2)" stroke-width="2"/>'
                       f'<text x="{sx(target) + 4:.1f}" y="{y - 4}" style="fill:var(--s2)">target {target}</text>')
        out.append(f'<line x1="{sx(lo):.1f}" y1="{y}" x2="{sx(hi):.1f}" y2="{y}" stroke="var(--s1)" stroke-width="2"/>'
                   f'<circle cx="{sx(median):.1f}" cy="{y}" r="5" fill="var(--s1)" stroke="var(--surface)" stroke-width="2">'
                   f'<title>{esc(item_label)}: {median:.3f} [{lo:.3f}, {hi:.3f}]</title></circle>'
                   f'<text x="{sx(hi) + 8:.1f}" y="{y + 4}">{median:.3f} [{lo:.3f}, {hi:.3f}]</text>')
    out.append('</svg>')
    out.append(table(['Comparison', 'Paired median', '95% CI low', '95% CI high', 'Target (≤)'],
                     [(n, f'{m:.3f}', f'{lo:.3f}', f'{hi:.3f}', t or '-') for n, m, (lo, hi), t in items],
                     f'Data for: {label}'))
    return ''.join(out)


def render_overview(d):
    b, c, a, p = d['benchmark'], d['controller'], d['accounting'], d['profiles']
    m = b['medians']
    ox, sk, vt = m['after/oximut'], m['after/stryker'], m['after/vitest']
    r = b['ratios']['after_vs_stryker']
    ph = b['phases']['after']
    # Wall anatomy from the controller capture (one run; shares, not absolutes across runs).
    jobs = c['jobs']
    baseline_jobs = [j for j in jobs if j['kind'] == 'baseline']
    mutant_jobs = [j for j in jobs if j['kind'] == 'mutant']
    forks = [f for j in jobs for f in j['forks']]
    boot = sum(f['started'] - f['fork'] for f in forks)
    collect = sum(f['collected'] - f['started'] for f in forks)
    run = sum(f['finished'] - f['collected'] for f in forks)
    stop = sum(f['exit'] - f['finished'] for f in forks)
    mut_run = sum(f['finished'] - f['collected'] for j in mutant_jobs for f in j['forks'])
    # Like-for-like: median test time of one killed-mutant job vs one passing baseline file.
    mut_job_median = statistics.median(sum(f['finished'] - f['collected'] for f in j['forks']) for j in mutant_jobs)
    base_file_median = statistics.median(f['finished'] - f['collected'] for j in baseline_jobs for f in j['forks'])
    oximut_wall_ms = c['tools']['oximut']['wall_seconds'] * 1000
    # Controller start runs until the initialize response (Node + Vitest import + createVitest).
    initialize = next(j for j in jobs if j['command'] == 'initialize')
    init = initialize['end'] - c['controller_start_ms']
    fork_rows = [
        ('Fork bootstrap (spawn → started)', boot, SLOTS[0]),
        ('Module fetch + collection', collect, SLOTS[1]),
        ('Running tests', run, SLOTS[2]),
        ('Stop → exit', stop, SLOTS[3]),
    ]
    accounted = boot + collect + run + stop + init
    rest = oximut_wall_ms - accounted
    sampler = a['sampler']['sampling']
    sampler_cpu = sampler['selfCpuNs'] / 1e9
    ac_ox = a['tools']['oximut']
    fork_cpu = sum(x['self_cpu_ms'].get('user', 0) + x['self_cpu_ms'].get('system', 0) for x in p['roles']['fork'])
    ctl_cpu = sum(x['self_cpu_ms'].get('user', 0) + x['self_cpu_ms'].get('system', 0) for x in p['roles']['controller'])
    fork_cats = collections.Counter()
    for x in p['roles']['fork']:
        fork_cats.update(x['categories'])
    busy = sum(v for k, v in fork_cats.items() if k != 'Idle (waiting)')
    test_share_text = share(fork_cats['colord source + tests'], busy)
    partial = (f' (partial: {len(p["roles"]["fork"])} of {p["forks_expected"]} fork profiles)'
               if len(p['roles']['fork']) != p['forks_expected'] else '')
    w = c['workload']
    body = []
    if not a['passed']:
        body.append('<p><b>Warning:</b> the accounting diagnostic did not pass; its sampler and memory values '
                    'below are not qualified evidence.</p>')
    body.append('<p>This report explains where Oximut spends wall time, CPU and memory on the colord '
                f'benchmark ({w["tests"]} tests, {w["mutated_files"]} mutated files, {w["killed"]} of {w["mutants"]} '
                'mutants killed, one worker), and what separates it from Stryker. Every number comes from retained '
                'native GitHub Actions evidence; the inputs and their hashes are listed at the bottom.</p>')
    body.append('<div class="tiles">'
                f'<div class="tile"><span>Wall vs Stryker (target ≤ 0.50)</span><b>{r["wall_seconds"]["median"]:.2f}×</b>'
                f'<span>{ox["wall_seconds"]:.2f} s vs {sk["wall_seconds"]:.2f} s</span></div>'
                f'<div class="tile"><span>CPU vs Stryker (target ≤ 0.80)</span><b>{r["cpu_seconds"]["median"]:.2f}×</b>'
                f'<span>{ox["cpu_seconds"]:.2f} s vs {sk["cpu_seconds"]:.2f} s</span></div>'
                f'<div class="tile"><span>Peak memory vs Stryker (target ≤ 0.80)</span><b>{r["peak_aggregate_rss_bytes"]["median"]:.2f}×</b>'
                f'<span>{ox["peak_aggregate_rss_bytes"] / 1e6:.0f} MB vs {sk["peak_aggregate_rss_bytes"] / 1e6:.0f} MB</span></div>'
                f'<div class="tile"><span>Real test work inside forks</span><b>{test_share_text}</b>'
                f'<span>of busy fork CPU is colord code + tests{esc(partial)}</span></div></div>')
    body.append('<h2>1. Where we stand</h2>')
    body.append(f'<p>Paired native campaign, {b["pairs"]} measured pairs after one warm-up; correctness and '
                'build-integrity gates ' + ('pass' if b['gate'] else '<b>fail</b>') +
                '. Dots are paired medians and lines are bootstrap 95% intervals. The dashed line is parity with Stryker '
                'and the orange tick is the target.</p>')
    body.append(ratio_chart([
        ('Wall time / Stryker', r['wall_seconds']['median'], r['wall_seconds']['ci'], 0.5),
        ('CPU (user+sys) / Stryker', r['cpu_seconds']['median'], r['cpu_seconds']['ci'], 0.8),
        ('Peak aggregate RSS / Stryker', r['peak_aggregate_rss_bytes']['median'], r['peak_aggregate_rss_bytes']['ci'], 0.8),
    ], 'Oximut divided by Stryker: paired medians with bootstrap 95% intervals and targets'))
    body.append(table(['Tool', 'Wall s', 'CPU s', 'user s', 'sys s', 'Peak RSS MB', 'Peak / observed processes'], [
        (name, f'{x["wall_seconds"]:.3f}', f'{x["cpu_seconds"]:.3f}', f'{x["cpu_user_seconds"]:.3f}',
         f'{x["cpu_system_seconds"]:.3f}', f'{x["peak_aggregate_rss_bytes"] / 1e6:.1f}',
         f'{x["peak_process_count"]:.0f} / {x["observed_process_count"]:.0f}')
        for name, x in [('Oximut', ox), ('Stryker', sk), ('plain Vitest (one full run)', vt)]],
        'Median per tool across the measured pairs (after candidate)'))
    body.append(f'<p><b>User CPU is already {"below" if ox["cpu_user_seconds"] < sk["cpu_user_seconds"] else "close to"} '
                f'Stryker\'s</b> ({ox["cpu_user_seconds"]:.2f} s vs {sk["cpu_user_seconds"]:.2f} s). The CPU gap is '
                f'kernel time: {ox["cpu_system_seconds"]:.2f} s vs {sk["cpu_system_seconds"]:.2f} s, from starting '
                f'{ox["observed_process_count"]:.0f} processes per run (Stryker starts {sk["observed_process_count"]:.0f}) '
                'and from Oximut\'s own memory sampler.</p>')
    body.append('<h2>2. Where the wall time goes</h2>')
    body.append('<p>Oximut phases (median of the paired campaign), next to Stryker\'s and plain Vitest\'s totals:</p>')
    body.append(stacked_bars([
        ('Oximut', [('Original baseline', ph['originalBaseline'], SLOTS[0]),
                    ('Instrumented baseline', ph['instrumentedBaseline'], SLOTS[1]),
                    ('Mutant execution (10)', ph['mutationExecution'], SLOTS[2]),
                    ('Teardown + other', ph['teardown'] + ph['inputAndSchedulerPreparation'], SLOTS[3])]),
        ('Stryker (total)', [('Stryker total', sk['wall_seconds'] * 1000, SLOTS[4])]),
        ('Plain Vitest (total)', [('Vitest total', vt['wall_seconds'] * 1000, SLOTS[5])]),
    ], ' ms', 'Oximut wall time by phase next to Stryker and plain Vitest totals'))
    body.append(legend([('Original baseline', SLOTS[0]), ('Instrumented baseline', SLOTS[1]),
                        ('Mutant execution', SLOTS[2]), ('Teardown + other', SLOTS[3]),
                        ('Stryker total', SLOTS[4]), ('Plain Vitest total', SLOTS[5])]))
    baselines_s = (ph['originalBaseline'] + ph['instrumentedBaseline']) / 1000
    target_s = sk['wall_seconds'] / 2
    body.append(f'<p>The two baselines alone take {baselines_s:.2f} s, '
                f'{100 * baselines_s / target_s:.0f}% of the whole target budget (≤ 0.5 × Stryker ≈ {target_s:.2f} s). '
                'Meeting the goal therefore requires shrinking the baselines as well as the per-mutant cost.</p>')
    body.append('<h3>Inside those phases: what every test process spends its time on</h3>')
    body.append(f'<p>The controller-job capture records each Vitest fork\'s lifecycle (18 forks: 4 original-baseline, '
                f'4 instrumented-baseline, 10 mutants). Totals across all forks of one run, in ms; the run took '
                f'{oximut_wall_ms:.0f} ms on that runner:</p>')
    body.append(stacked_bars([
        ('All 18 forks', [(n, v, col) for n, v, col in fork_rows]),
        ('Controller start', [('Node + Vitest import + createVitest', init, SLOTS[4])]),
        ('Rest of the run', [('Rust CLI, slot, IPC, teardown', max(rest, 0), SLOTS[5])]),
    ], ' ms', 'One diagnostic run: fork lifecycle totals, controller start and the rest'))
    body.append(legend([(n, col) for n, _, col in fork_rows] +
                       [('Controller start', SLOTS[4]), ('Rust CLI, slot, IPC, teardown', SLOTS[5])]))
    body.append(f'<p><b>Fork bootstrap is the largest single block</b> ({boot:.0f} ms, '
                f'{100 * boot / oximut_wall_ms:.0f}% of the run): every fresh process starts Node and loads the '
                f'Vitest worker runtime before it can run a single test. In the 10 killed-mutant jobs, running the '
                f'(now failing) tests takes {mut_run:.0f} ms in total: a median of {mut_job_median:.0f} ms per job, '
                f'{mut_job_median / base_file_median:.1f}× the {base_file_median:.0f} ms median of a passing baseline file. '
                'Failure reporting (assertion diffs, stack traces) is the likely cause; this is a hypothesis to measure. '
                'See the <a href="timeline.html">job timeline</a>.</p>')
    body.append('<h2>3. Ranked bottlenecks</h2>')
    ranks = [
        ('Fresh-process tax: 18 Node processes per run',
         f'{boot:.0f} ms wall in bootstrap ({100 * boot / oximut_wall_ms:.0f}% of the run); forks consume '
         f'{share(fork_cpu, fork_cpu + ctl_cpu)} of Node CPU{partial}, yet only {test_share_text} of busy fork CPU is colord code '
         'and tests. The rest is Node bootstrap, module loading/compile, Vitest/expect/chai setup and GC, repeated in every process.',
         ['Build a V8 startup snapshot of the Vitest fork runtime (node --snapshot-blob), so each fresh '
          'process starts with the runtime already initialized. This keeps the fresh-process guarantee.',
          'Opt-in warm runtime: strictly opt-in, per the owner\'s decision; default stays fresh forks.',
          'Trim per-fork setup: only load what the selected test file needs (setup files, reporters, coverage hooks).']),
        ('Two full baselines before the first mutant',
         f'{(ph["originalBaseline"] + ph["instrumentedBaseline"]) / 1000:.2f} s of wall per run: controller start '
         f'(~{init:.0f} ms in the capture) plus 8 forks that each run one test file.',
         ['Run both baselines in one Vitest run (8 specs in one call) to share pool and controller warm-up.',
          'Start the instrumented baseline while the original one is still running (overlap, same total CPU).',
          'Keep the original baseline as the oracle, and question whether every file needs both runs.']),
        ('Killed mutants likely pay for failure formatting',
         f'Median {mut_job_median:.0f} ms of test time per killed-mutant job, {mut_job_median / base_file_median:.1f}× the '
         f'{base_file_median:.0f} ms median per passing baseline file ({mut_run:.0f} ms across {len(mutant_jobs)} mutant jobs).',
         ['Bail on the first failure (Stryker uses bail: 1): the mutant is already killed.',
          'Skip diff and stack formatting in mutant jobs (chai truncateThreshold, diff off).']),
        ('Oximut\'s own memory sampler',
         f'{sampler_cpu:.2f} s CPU per run ({100 * sampler_cpu / ac_ox["cpu_seconds"]:.0f}% of Oximut\'s CPU in that '
         f'capture), {sampler["calls"]} samples on the scheduler thread, almost all kernel time in smaps_rollup page walks.',
         ['Sample less often when nothing can be admitted (one worker, no memory pressure).',
          'Drop periodic samples on the dispatch path; keep forced samples at admission/response.',
          'Read cgroup memory.current when Oximut owns a cgroup (one cheap read per sample).']),
        ('Controller transform work on the critical path',
         'The controller spends its busy time on Vite transforms, inline source-map scanning (regex over '
         'module text) and spawning forks, while the fork waits in its fetch/collect step.',
         ['Pre-transform resident modules once and serve cached results (already partly done by instrument-once).',
          'Disable source maps for mutant jobs where no coverage or stack mapping is needed.']),
    ]
    for i, (title, evidence, ideas) in enumerate(ranks, 1):
        body.append(f'<div class="rank"><h3>#{i} {esc(title)}</h3><p>{esc(evidence)}</p><ul>' +
                    ''.join(f'<li>{esc(x)}</li>' for x in ideas) + '</ul></div>')
    body.append('<p class="note">The ideas listed are discussion candidates, not measured savings. Each would be its '
                'own PR with a paired native benchmark and the owner\'s rule: faster with no added CPU or memory.</p>')
    body.append('<h2>Evidence and limits</h2><ul>' + ''.join(f'<li>{x}</li>' for x in [
        f'Paired campaign: run {esc(d["runs"]["benchmark"])} (scored; ratios re-derived from raw rows; '
        'source of every ratio and phase median).',
        f'Controller job timeline: run {esc(d["runs"]["controller"])} at {esc(c["identity"]["commit"][:7])} '
        f'(one overhead-bearing run; passed: {c["identity"]["passed"]}).',
        f'Rust sampler accounting: run {esc(d["runs"]["accounting"])} at {esc(a["identity"]["commit"][:7])} '
        f'(one run; CLI sampling thread only; passed: {a["identity"]["passed"]}).',
        f'Node CPU profiles: run {esc(d["runs"]["profiles"])} at {esc(p["identity"]["commit"][:7])}, a merge of main '
        'with the #43 profiler tooling. Profiler overhead included; '
        f'{len(p["roles"]["fork"])} of {p["forks_expected"]} fork profiles present (passed: {p["identity"]["passed"]}; '
        'used as partial evidence).',
        f'All four runs declare the same workload ({esc(b["workload_sha256"][:12])}).',
        'Diagnostic runs can land on faster or slower runner hosts. Never subtract absolute times across captures.',
    ]) + '</ul>')
    body.append('<details><summary>Input files and SHA-256</summary>' +
                table(['File', 'SHA-256'], sorted(d['inputs'].items()), 'Every consumed input file') + '</details>')
    return page('Oximut bottleneck report', ''.join(body), d['generated'])


def render_timeline(d):
    c = d['controller']
    jobs = [j for j in c['jobs'] if j['forks']]
    t0 = c['controller_start_ms']
    t1 = max(f['exit'] for j in jobs for f in j['forks'])
    width, label_w, row = 1000, 190, 22
    plot = width - label_w - 20
    sx = lambda t: label_w + plot * (t - t0) / (t1 - t0)
    forks = [(j, f) for j in jobs for f in j['forks']]
    height = len(forks) * row + 60
    colors = [('Bootstrap (spawn → started)', 'fork', 'started', SLOTS[0]),
              ('Fetch + collect', 'started', 'collected', SLOTS[1]),
              ('Run tests', 'collected', 'finished', SLOTS[2]),
              ('Stop → exit', 'finished', 'exit', SLOTS[3])]
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="Gantt chart: lifecycle of each of the {len(forks)} Vitest forks in one run">']
    for tick in range(0, int(t1 - t0) + 1, 250):
        x = sx(t0 + tick)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 40}" stroke="var(--grid)"/>'
                   f'<text x="{x:.1f}" y="{height - 24}" text-anchor="middle">{tick} ms</text>')
    first = next(j for j in c['jobs'] if j['command'] == 'initialize')['end']
    out.append(f'<rect x="{sx(t0):.1f}" y="2" width="{sx(first) - sx(t0):.1f}" height="{height - 46}" '
               f'fill="var(--s5)" opacity="0.18"><title>Controller start: Node + Vitest import + createVitest '
               f'({first - t0:.0f} ms)</title></rect>')
    seen = collections.Counter()
    fork_labels = []
    for i, (job, fork) in enumerate(forks):
        y = i * row + 4
        seen[id(job)] += 1
        label = (f'mutant {job["mutant"][:8]}' if job['kind'] == 'mutant' else
                 f'baseline {1 + [k for k in jobs if k["kind"] == "baseline"].index(job)} · file {seen[id(job)]}')
        fork_labels.append(label)
        out.append(f'<text class="lbl" x="{label_w - 8}" y="{y + 13}" text-anchor="end">{esc(label)}</text>')
        for name, a, b, color in colors:
            x0, x1 = sx(fork[a]), sx(fork[b])
            out.append(f'<rect x="{x0:.1f}" y="{y}" width="{max(x1 - x0 - 1, 0.6):.1f}" height="{row - 6}" rx="3" '
                       f'fill="{color}"><title>{esc(label)} - {esc(name)}: {fork[b] - fork[a]:.1f} ms</title></rect>')
    out.append('</svg>')
    seg = collections.defaultdict(list)
    for job, fork in forks:
        kind = 'mutant' if job['kind'] == 'mutant' else 'baseline'
        for name, a, b, _ in colors:
            seg[(kind, name)].append(fork[b] - fork[a])
    run_ms = c['tools']['oximut']['wall_seconds'] * 1000
    totals = collections.Counter()
    for (kind, name), v in seg.items():
        totals[name] += sum(v)
    rows = [(f'{kind}: {name}', f'{statistics.median(v):.1f}', f'{min(v):.1f}', f'{max(v):.1f}', len(v))
            for (kind, name), v in sorted(seg.items())]
    share_rows = [(name, f'{v:.0f}', share(v, run_ms)) for name, v in totals.most_common()]
    fork_rows = [(label, f'{fork["fork"] - t0:.1f}') + tuple(f'{fork[b] - fork[a]:.1f}' for _, a, b, _ in colors)
                 for label, (job, fork) in zip(fork_labels, forks, strict=True)]
    job_rows = [(f'{j["kind"]} {(j["mutant"] or "")[:8]}', f'{j["end"] - j["start"]:.1f}', len(j['forks']),
                 f'{j["forks"][0]["fork"] - j["start"]:.1f}', f'{j["controller_cpu_ms"]:.0f}') for j in jobs]
    baselines = [j for j in jobs if j['kind'] == 'baseline']
    mutants = [j for j in jobs if j['kind'] == 'mutant']
    init_end = first - t0
    recon = [
        ('originalBaseline', f'{c["phases"]["originalBaseline"]:.0f}',
         f'{init_end + baselines[0]["end"] - baselines[0]["start"]:.0f}',
         'controller start + initialize + first baseline job'),
        ('instrumentedBaseline', f'{c["phases"]["instrumentedBaseline"]:.0f}',
         f'{baselines[1]["end"] - baselines[1]["start"]:.0f}', 'second baseline job'),
        ('mutationExecution', f'{c["phases"]["mutationExecution"]:.0f}',
         f'{sum(j["end"] - j["start"] for j in mutants):.0f}', f'sum of {len(mutants)} mutant jobs'),
    ]
    # Same Stryker code in both: report the ratio, not a host-speed conclusion (one unwarmed run vs a
    # warmed median can differ for many reasons).
    host = c['tools']['stryker']['wall_seconds'] / d['benchmark']['medians']['after/stryker']['wall_seconds']
    body = ['<p><b>One diagnostic run</b> from the controller-job capture. In it, Stryker took '
            f'{100 * host:.0f}% of the scored campaign\'s median Stryker wall time (same Stryker code; one '
            'unwarmed run versus a warmed median). Absolute milliseconds are valid only within this run; use '
            'the shares to compare with other evidence. Each row is one fresh Vitest fork, coloured by lifecycle '
            'step; hover a segment for its duration. The shaded band is controller start-up, up to the initialize '
            'response.</p>',
            out[0] + ''.join(out[1:]),
            legend([(n, col) for n, _, _, col in colors] + [('Controller start', 'var(--s5)')]),
            '<details><summary>Data table</summary>' +
            table(['Fork', 'start ms'] + [n for n, _, _, _ in colors], fork_rows,
                  f'Data for: Gantt chart: lifecycle of each of the {len(forks)} Vitest forks in one run') +
            '</details>',
            '<h2>Share of the run by fork step</h2>',
            table(['Step (all forks)', 'ms', f'share of the {run_ms:.0f} ms run'], share_rows,
                  'Fork lifecycle steps summed across all forks'),
            '<h2>Per-step durations (ms)</h2>',
            table(['Step', 'median', 'min', 'max', 'n'], rows, 'Per-fork step durations within this run'),
            '<h2>Jobs</h2>',
            table(['Job', 'wall ms', 'forks', 'request → fork ms', 'controller CPU ms'], job_rows,
                  'Controller-visible job walls within this run'),
            '<h2>Rust phases vs controller-visible time (same run)</h2>',
            table(['Rust phase', 'Rust wall ms', 'controller-visible ms', 'controller-visible part'], recon,
                  'The difference is Rust CLI, slot process, IPC and scheduling outside the controller'),
            f'<p class="note">Run {esc(d["runs"]["controller"])}. The first mutant also carries the controller '
            'CPU-profile window of this diagnostic mode.</p>']
    return page('Oximut job timeline', ''.join(body), d['generated'])


def render_cpu(d):
    p, a, c = d['profiles'], d['accounting'], d['controller']
    order = ['colord source + tests', 'Vitest runtime (runner/expect/chai)', 'Node module loading/compile',
             'Node bootstrap/runtime', 'GC', 'Vite transform/resolve', 'V8/native runtime',
             'Oximut adapter', 'Other dependencies', 'Idle (waiting)']
    color = dict(zip(order, SLOTS + ['var(--muted)'] * 4))
    def totals(role):
        acc = collections.Counter()
        for x in p['roles'][role]:
            acc.update(x['categories'])
        return acc
    fork_t, ctl_t = totals('fork'), totals('controller')
    main6 = order[:6]
    def segs(acc):
        out = [(k, acc.get(k, 0) / 1e3, color[k]) for k in main6]
        out.append(('Everything else (busy)', sum(v for k, v in acc.items() if k not in main6 and k != 'Idle (waiting)') / 1e3, 'var(--muted)'))
        return out
    present, expected = len(p['roles']['fork']), p['forks_expected']
    body = [f'<p>Sampled Node CPU profiles from run {esc(d["runs"]["profiles"])} at '
            f'{esc(p["identity"]["commit"][:7])} (main merged with the #43 profiler tooling; main thread only, '
            'profiler overhead included). The categories group self time by where the code lives. Idle time is '
            'excluded from the bars and shown in the table.</p>']
    if present != expected:
        body.append(f'<p><b>Partial evidence:</b> {present} of {expected} fork profiles were captured, so fork totals '
                    'and per-fork averages cover only the captured forks. Shares within them remain comparable.</p>')
    body.append(stacked_bars([(f'{present} forks (sum)', segs(fork_t)),
                              ('Controller', segs(ctl_t))], ' ms',
                             'Busy Node CPU by category: forks versus controller'))
    body.append(legend([(k, color[k]) for k in main6] + [('Everything else (busy)', 'var(--muted)')]))
    rows = []
    fb = sum(v for k, v in fork_t.items() if k != 'Idle (waiting)')
    cb = sum(v for k, v in ctl_t.items() if k != 'Idle (waiting)')
    for k in order:
        idle = k == 'Idle (waiting)'
        rows.append((k, f'{fork_t.get(k, 0) / 1e3:.1f}', '-' if idle else share(fork_t.get(k, 0), fb),
                     f'{ctl_t.get(k, 0) / 1e3:.1f}', '-' if idle else share(ctl_t.get(k, 0), cb)))
    body.append(table(['Category', 'forks ms', 'share of busy', 'controller ms', 'share of busy'], rows,
                      'All categories, including idle time'))
    for role in ('fork', 'controller'):
        fn = collections.Counter()
        for x in p['roles'][role]:
            for cat, name, where, us in x['functions']:
                if cat != 'Idle (waiting)':
                    fn[(cat, name, where)] += us
        body.append(f'<h3>Top self-time functions: {"forks" if role == "fork" else "controller"}</h3>')
        body.append(table(['Function', 'Where', 'Category', 'ms'],
                          [(n, w, cat, f'{us / 1e3:.1f}') for (cat, n, w), us in fn.most_common(15)],
                          f'Top 15 busy self-time functions ({role})'))
    cpu_rows = []
    for role in ('controller', 'fork'):
        xs = p['roles'][role]
        cpu_rows.append((role if role == 'controller' else f'{len(xs)} forks',
                         f'{sum(x["self_cpu_ms"].get("user", 0) for x in xs):.0f}',
                         f'{sum(x["self_cpu_ms"].get("system", 0) for x in xs):.0f}',
                         f'{sum(x["self_cpu_ms"].get("user", 0) + x["self_cpu_ms"].get("system", 0) for x in xs) / max(len(xs), 1):.0f}'))
    body.append('<h2>Process CPU (getrusage at exit, profiled run)</h2>')
    body.append(table(['Process', 'user ms', 'sys ms', 'per process ms'], cpu_rows,
                      'Whole-process CPU (all threads) at exit; captured processes only'))
    s = a['sampler']
    body.append('<h2>Oximut\'s Rust memory sampler</h2>')
    if not a['passed']:
        body.append('<p><b>Warning:</b> this accounting diagnostic did not pass; values are not qualified.</p>')
    body.append(table(['Stage', 'calls', 'thread CPU ms', 'kernel ms', 'wall ms'], [
        (name, s[key]['calls'], f'{s[key]["selfCpuNs"] / 1e6:.1f}', f'{s[key]["selfSystemNs"] / 1e6:.1f}',
         f'{s[key]["wallNs"] / 1e6:.1f}') for name, key in
        [('All sampling', 'sampling'), ('Process discovery', 'discovery'), ('Identity reads', 'identityReads'),
         ('RSS reads (smaps_rollup)', 'rssReads')]],
        'CLI sampling-thread clocks; stages nest inside all sampling'))
    body.append(f'<p>Same run: Oximut used {a["tools"]["oximut"]["cpu_seconds"]:.2f} s CPU '
                f'({a["tools"]["oximut"]["cpu_system_seconds"]:.2f} s kernel). The sampler accounts for '
                f'{100 * s["sampling"]["selfCpuNs"] / 1e9 / a["tools"]["oximut"]["cpu_seconds"]:.0f}% of it.</p>')
    body.append('<h2>Memory at each tool\'s peak</h2>')
    snap_rows = []
    for snap in a['peak_rss_snapshots']:
        for proc in snap['processes']:
            snap_rows.append((snap['tool'], proc['comm'], proc['pid'], f'{proc["rss_bytes"] / 1e6:.1f}'))
    body.append(stacked_bars([(snap['tool'], [(f'{pr["comm"]} {pr["pid"]}', pr['rss_bytes'] / 1e6, SLOTS[i % 6])
                                              for i, pr in enumerate(sorted(snap['processes'], key=lambda q: -q['rss_bytes']))])
                              for snap in a['peak_rss_snapshots']], ' MB',
                             'Resident memory per process at each tool\'s aggregate peak'))
    body.append(table(['Tool', 'Process', 'PID', 'RSS MB'], snap_rows, 'Processes in each peak snapshot'))
    body.append('<p>Oximut\'s peak is one controller (Vite + Vitest in-process) plus one test fork. Stryker keeps '
                'its main process, a checker and a long-lived test runner. Memory is the one target already met.</p>')
    body.append(f'<p class="note">Profiles: run {esc(d["runs"]["profiles"])}; accounting: run {esc(d["runs"]["accounting"])}; '
                f'controller self CPU in the un-profiled controller capture: '
                f'{c["controller_self_cpu_ms"]["user"]:.0f} ms user / {c["controller_self_cpu_ms"]["system"]:.0f} ms sys.</p>')
    return page('Oximut CPU and memory', ''.join(body), d['generated'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='stage', required=True)
    ex = sub.add_parser('extract')
    for name in ('benchmark', 'accounting', 'controller', 'profiles'):
        ex.add_argument(f'--{name}', required=True, type=Path)
        ex.add_argument(f'--{name}-run', required=True)
    ex.add_argument('--generated', required=True, help='provenance line shown on every page')
    ex.add_argument('--output', required=True, type=Path)
    rd = sub.add_parser('render')
    rd.add_argument('--data', required=True, type=Path)
    rd.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.stage == 'extract':
        inputs = {}
        data = {'generated': args.generated,
                'runs': {k: getattr(args, f'{k}_run') for k in ('benchmark', 'accounting', 'controller', 'profiles')},
                'benchmark': extract_benchmark(args.benchmark, inputs),
                'accounting': extract_accounting(args.accounting, inputs),
                'controller': extract_controller(args.controller, inputs),
                'profiles': extract_profiles(args.profiles, inputs)}
        workloads = {data['benchmark']['workload_sha256']} | {
            data[k]['identity']['workload_sha256'] for k in ('accounting', 'controller', 'profiles')}
        if len(workloads) != 1:
            raise SystemExit(f'inputs declare different workloads: {sorted(workloads)}')
        roots = {k: str(getattr(args, k).resolve()) for k in ('benchmark', 'accounting', 'controller', 'profiles')}
        def rel(path):
            path = str(Path(path).resolve())
            for name, root in roots.items():
                if path.startswith(root + os.sep):
                    return f'{name} {data["runs"][name]}: {os.path.relpath(path, root)}'
            return path
        data['inputs'] = {rel(k): v for k, v in inputs.items()}
        write_atomic(args.output, json.dumps(data, indent=1, sort_keys=True) + '\n')
    else:
        data = json.loads(args.data.read_text(encoding='utf-8'))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # Render every page before replacing any, so a failure leaves the previous set intact.
        pages = {'index.html': render_overview(data), 'timeline.html': render_timeline(data),
                 'cpu.html': render_cpu(data)}
        for name, text in pages.items():
            write_atomic(args.output_dir / name, text)


if __name__ == '__main__':
    main()
