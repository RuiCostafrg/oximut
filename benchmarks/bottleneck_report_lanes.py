#!/usr/bin/env python3
"""Explain where a matched-concurrency run with shared Vitest lanes spends its wall time.

This is the shared-lane successor of bottleneck_report.py (whose 2026-11-07 report covers one
controller per worker at one worker). Same two stages, same offline pages:

  extract  native artifact directories -> data.json (derived numbers + input hashes)
  render   data.json -> index.html, timeline.html, cpu.html (offline, no scripts needed)

Inputs (downloaded with `gh run download <id>` into new empty directories):
  --benchmark     real-project before/after campaign at the matched concurrency (scored)
  --benchmark-low same campaign at a lower concurrency (scored; for scaling)
  --controller    native-diagnostic capture, mode controller-jobs, at the matched concurrency
  --profiles      native-diagnostic capture, mode cpu-prof-exec-argv, at the matched concurrency

Diagnostic captures are single overhead-bearing runs on possibly different runner hosts: the
report compares shares and durations inside one capture and never subtracts absolute times across
captures. Savings estimates are upper bounds derived from one capture, not measurements.
"""
import argparse
import collections
import copy
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bottleneck_report as base  # noqa: E402

SCHEMA = 2
SLOTS = base.SLOTS
esc, table, share, legend, stacked_bars, ratio_chart = (
    base.esc, base.table, base.share, base.legend, base.stacked_bars, base.ratio_chart)


# ----------------------------------------------------------------------------- extract

def check_inputs(concurrency, benchmark, benchmark_low, controller, profiles):
    """Refuse evidence that does not describe one workload at one matched shared-lane concurrency."""
    if concurrency['benchmark'] != concurrency['controller'] or concurrency['controller'] != concurrency['profiles']:
        raise SystemExit(f'inputs ran at different concurrency: {concurrency}')
    if not isinstance(concurrency['benchmark'], int) or concurrency['benchmark'] < 2:
        raise SystemExit(f'lane evidence needs matched concurrency of at least 2: {concurrency}')
    if not concurrency['benchmark_low'] or concurrency['benchmark_low'] >= concurrency['benchmark']:
        raise SystemExit(f'the low campaign must run fewer workers: {concurrency}')
    workloads = ({benchmark['workload_sha256'], benchmark_low['workload_sha256']} |
                 {controller['identity']['workload_sha256'], profiles['identity']['workload_sha256']})
    if len(workloads) != 1:
        raise SystemExit(f'inputs declare different workloads: {sorted(workloads)}')
    if not controller['identity']['passed']:
        raise SystemExit('the controller capture did not pass; refusing a timeline built on it')
    if not any(j['lanes'] for j in controller['jobs']):
        raise SystemExit('the controller capture shows no shared-lane batches')
    if sum(j['kind'] == 'baseline' and bool(j['forks']) for j in controller['jobs']) != 2:
        raise SystemExit('the controller capture needs exactly one original and one instrumented baseline job')


def extract(args):
    inputs = {}
    benchmark = base.extract_benchmark(args.benchmark, inputs)
    benchmark_low = base.extract_benchmark(args.benchmark_low, inputs)
    controller = base.extract_controller(args.controller, inputs)
    profiles = base.extract_profiles(args.profiles, inputs)
    concurrency = {name: base.read_json(base.one(root, 'summary.json'), inputs).get('concurrency')
                   for name, root in (('benchmark', args.benchmark), ('benchmark_low', args.benchmark_low))}
    for name, root in (('controller', args.controller), ('profiles', args.profiles)):
        concurrency[name] = base.read_json(base.one(root, 'capture/diagnostic.json'), inputs).get('concurrency')
    check_inputs(concurrency, benchmark, benchmark_low, controller, profiles)
    runs = {'benchmark': args.benchmark_run, 'benchmark_low': args.benchmark_low_run,
            'controller': args.controller_run, 'profiles': args.profiles_run}
    roots = {k: str(getattr(args, k).resolve()) for k in runs}

    def rel(path):
        path = str(Path(path).resolve())
        for name, root in roots.items():
            if path.startswith(root + os.sep):
                return f'{name} {runs[name]}: {os.path.relpath(path, root)}'
        return path
    return {'schema': SCHEMA, 'generated': args.generated, 'runs': runs, 'concurrency': concurrency['benchmark'],
            'concurrency_low': concurrency['benchmark_low'], 'benchmark': benchmark, 'benchmark_low': benchmark_low,
            'controller': controller, 'profiles': profiles,
            'inputs': {rel(k): v for k, v in inputs.items()}}


# ----------------------------------------------------------------------------- analysis

def fork_step(fork, name):
    a, b = STEPS[name]
    return fork[b] - fork[a]


STEPS = {'Bootstrap (spawn → started)': ('fork', 'started'), 'Fetch + collect': ('started', 'collected'),
         'Run tests': ('collected', 'finished'), 'Stop → exit': ('finished', 'exit')}


def job_label(jobs, job):
    runs = [j for j in jobs if j['forks']]
    kinds = collections.Counter()
    for j in runs:
        kinds[j['kind']] += 1
        if j is job:
            break
    if job['kind'] == 'mutant':
        return f'mutant batch {kinds["mutant"]}', len(job['forks'])
    return ('original baseline' if kinds['baseline'] == 1 else 'instrumented baseline'), len(job['forks'])


def analyse(d):
    """Everything the pages state, derived from the stored evidence (shares and capture-local ms)."""
    c, workers = copy.deepcopy(d['controller']), d['concurrency']
    jobs = c['jobs']
    runs = [j for j in jobs if j['forks']]
    wall = c['tools']['oximut']['wall_seconds'] * 1000
    initialize = next(j for j in jobs if j['command'] == 'initialize')
    boot_ms = initialize['end'] - c['controller_start_ms']
    for j in runs:
        j['_t0'] = min(f['fork'] for f in j['forks'])
        j['_wall'] = j['end'] - j['start']
        j['_label'] = job_label(jobs, j)[0]
    gaps = []
    ordered = sorted(jobs, key=lambda j: j['start'])
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        gaps.append((f'{prev["_label"] if prev["forks"] else prev["command"]} → '
                     f'{nxt["_label"] if nxt["forks"] else nxt["command"]}', nxt['start'] - prev['end']))
    gap_ms = sum(g for _, g in gaps)
    mutants = [j for j in runs if j['kind'] == 'mutant']
    baselines = [j for j in runs if j['kind'] == 'baseline']
    critical_boot = [max(fork_step(f, 'Bootstrap (spawn → started)') for f in j['forks']) for j in runs]
    # A fork pool could hide every bootstrap but the first job's.
    hidable_boot = sum(critical_boot[1:])
    full = [j for j in mutants if j['lanes'] == workers]
    cold_excess = (max(full[0]['_wall'] - statistics.median(j['_wall'] for j in full[1:]), 0.0)
                   if len(full) > 1 else 0.0)
    # Lane time nobody used after the cold batch: lanes x batch wall minus the forks' lifetimes.
    idle_lane_ms = sum(workers * j['_wall'] - sum(f['exit'] - f['fork'] for f in j['forks'])
                       for j in mutants[1:])
    mutant_gaps = sum(g for name, g in gaps if name.startswith('mutant batch') and '→ mutant batch' in name)
    teardown = c['phases']['teardown']
    close = next(j for j in jobs if j['command'] == 'close')
    estimates = [
        ('Fresh-fork bootstrap on the critical path',
         hidable_boot, f'{sum(critical_boot):.0f} ms of every run waits for the slowest fork of each job to boot '
                       f'(max of spawn → started per job: {", ".join(f"{b:.0f}" for b in critical_boot)} ms). '
                       'Starting the next job\'s forks while the current one runs would hide all but the first job\'s.'),
        ('Instrumented baseline runs after the original baseline',
         baselines[1]['_wall'], f'The second baseline job takes {baselines[1]["_wall"]:.0f} ms of controller-visible '
                                f'time (controller CPU {baselines[1]["controller_cpu_ms"]:.0f} ms). '
                                'Overlapping it with the original baseline is bounded by the controller CPU they share.'),
        ('First mutant batch pays for cold lanes',
         cold_excess, f'The first full batch takes {full[0]["_wall"]:.0f} ms against a median of '
                      f'{statistics.median(j["_wall"] for j in full[1:]):.0f} ms for the later full batches; '
                      f'its controller CPU is {full[0]["controller_cpu_ms"]:.0f} ms of that wall.'
         if len(full) > 1 else 'No later full batch to compare against.'),
        ('Controller boot before the first job',
         boot_ms, f'{initialize["start"] - c["controller_start_ms"]:.0f} ms from Node start to the initialize request plus '
                  f'{initialize["end"] - initialize["start"]:.0f} ms to answer it (Node, Vitest import, createVitest).'),
        ('Idle lanes and gaps between mutant batches',
         idle_lane_ms / workers + mutant_gaps,
         f'After the first batch, lanes sit idle for {idle_lane_ms:.0f} lane-ms (the last batch runs '
         f'{mutants[-1]["lanes"]} of {workers} lanes); consecutive batches are {mutant_gaps:.0f} ms apart in total.'),
        ('Teardown after the controller closes',
         max(teardown - (close['end'] - close['start']), 0.0),
         f'Rust-side teardown is {teardown:.0f} ms of which the controller close is '
         f'{close["end"] - close["start"]:.0f} ms; the rest is verified descendant cleanup.'),
    ]
    estimates.sort(key=lambda e: -e[1])
    return {'wall': wall, 'boot_ms': boot_ms, 'gaps': gaps, 'gap_ms': gap_ms, 'estimates': estimates,
            'runs': runs, 'mutants': mutants, 'baselines': baselines, 'critical_boot': critical_boot,
            'idle_lane_ms': idle_lane_ms, 'cold_excess': cold_excess}


# ----------------------------------------------------------------------------- render

def fmt_ci(x):
    return f'{x["median"]:.3f} [{x["ci"][0]:.3f}, {x["ci"][1]:.3f}]'


def render_overview(d):
    b, lo, c, p = d['benchmark'], d['benchmark_low'], d['controller'], d['profiles']
    w, wl = d['concurrency'], d['concurrency_low']
    a = analyse(d)
    ox, sk = b['medians']['after/oximut'], b['medians']['after/stryker']
    oxl, skl = lo['medians']['after/oximut'], lo['medians']['after/stryker']
    ph, phl = b['phases']['after'], lo['phases']['after']
    r, rl = b['ratios']['after_vs_stryker'], lo['ratios']['after_vs_stryker']
    wall_ms = ox['wall_seconds'] * 1000
    target_ms = sk['wall_seconds'] * 500
    gap = wall_ms - target_ms
    workload = c['workload']
    body = [f'<p>Where Oximut\'s time goes on the colord benchmark ({workload["tests"]} tests, '
            f'{workload["mutated_files"]} mutated files, {workload["killed"]} of {workload["mutants"]} mutants killed) '
            f'at {w} workers with the shared Vitest controller, on main. Scored numbers come from paired native '
            f'campaigns; the breakdowns come from one controller-job capture and one CPU-profile capture at {w} workers. '
            'Inputs and hashes are at the bottom.</p>']
    body.append('<div class="tiles">'
                f'<div class="tile"><span>Wall vs Stryker at {w} workers (target ≤ 0.50)</span><b>{r["wall_seconds"]["median"]:.3f}×</b>'
                f'<span>{ox["wall_seconds"]:.2f} s vs {sk["wall_seconds"]:.2f} s</span></div>'
                f'<div class="tile"><span>Still to cut to reach 0.50×</span><b>{gap:.0f} ms</b>'
                f'<span>{100 * gap / wall_ms:.0f}% of the {wall_ms:.0f} ms run</span></div>'
                f'<div class="tile"><span>Mutant phase ({workload["mutants"]} mutants)</span><b>{ph["mutationExecution"]:.0f} ms</b>'
                f'<span>{share(ph["mutationExecution"], wall_ms)} of the run</span></div>'
                f'<div class="tile"><span>Two baselines</span><b>{ph["originalBaseline"] + ph["instrumentedBaseline"]:.0f} ms</b>'
                f'<span>{share(ph["originalBaseline"] + ph["instrumentedBaseline"], wall_ms)} of the run</span></div></div>')
    body.append('<h2>1. Where we stand</h2>')
    body.append(f'<p>Paired native campaigns, {b["pairs"]} measured pairs after one warm-up; correctness and build-integrity '
                'gates ' + ('pass' if b['gate'] and lo['gate'] else '<b>fail</b>') + '. Dots are paired medians and lines '
                'are bootstrap 95% intervals; the dashed line is parity with Stryker, the orange tick the target.</p>')
    body.append(ratio_chart([
        (f'Wall / Stryker, {w} workers', r['wall_seconds']['median'], r['wall_seconds']['ci'], 0.5),
        (f'Wall / Stryker, {wl} workers', rl['wall_seconds']['median'], rl['wall_seconds']['ci'], 0.5),
    ], f'Oximut wall time divided by Stryker at {w} and {wl} workers with targets'))
    body.append(table(['Run', 'Wall s', 'CPU s', 'user s', 'sys s', 'Peak RSS MB', 'Peak / observed processes'], [
        (f'Oximut, {n} workers', f'{x["wall_seconds"]:.3f}', f'{x["cpu_seconds"]:.3f}', f'{x["cpu_user_seconds"]:.3f}',
         f'{x["cpu_system_seconds"]:.3f}', f'{x["peak_aggregate_rss_bytes"] / 1e6:.0f}',
         f'{x["peak_process_count"]:.0f} / {x["observed_process_count"]:.0f}')
        for n, x in ((w, ox), (wl, oxl))] + [
        (f'Stryker, {n} workers', f'{x["wall_seconds"]:.3f}', f'{x["cpu_seconds"]:.3f}', f'{x["cpu_user_seconds"]:.3f}',
         f'{x["cpu_system_seconds"]:.3f}', f'{x["peak_aggregate_rss_bytes"] / 1e6:.0f}',
         f'{x["peak_process_count"]:.0f} / {x["observed_process_count"]:.0f}')
        for n, x in ((w, sk), (wl, skl))], 'Median per tool across the measured pairs (after candidate)'))
    speedup = ph['mutationExecution'] / phl['mutationExecution']
    body.append(f'<p><b>Doubling the workers barely helps.</b> Going from {wl} to {w} workers moves the mutant phase from '
                f'{phl["mutationExecution"]:.0f} ms to {ph["mutationExecution"]:.0f} ms ({speedup:.2f}×) and the '
                f'baselines from {phl["originalBaseline"] + phl["instrumentedBaseline"]:.0f} ms to '
                f'{ph["originalBaseline"] + ph["instrumentedBaseline"]:.0f} ms. The two campaigns ran on different runner hosts, so '
                'this comparison is indicative; it still says the lane count is not what limits the run. Sections 2 and 3 '
                'show what is.</p>')
    body.append('<h2>2. Where the wall time goes</h2>')
    segs = lambda x: [('Original baseline', x['originalBaseline'], SLOTS[0]),
                      ('Instrumented baseline', x['instrumentedBaseline'], SLOTS[1]),
                      ('Mutant execution', x['mutationExecution'], SLOTS[2]),
                      ('Teardown + other', x['teardown'] + x['inputAndSchedulerPreparation'], SLOTS[3])]
    body.append('<p>Oximut bars are sums of the independently computed phase medians, so they can differ slightly from '
                'the scored wall medians in the table above; Stryker bars are its scored wall medians.</p>')
    body.append(stacked_bars([(f'Oximut, {w} workers (phase sum)', segs(ph)),
                              (f'Oximut, {wl} workers (phase sum)', segs(phl)),
                              (f'Stryker, {w} workers', [('Stryker total', sk['wall_seconds'] * 1000, SLOTS[4])]),
                              (f'Stryker, {wl} workers', [('Stryker total', skl['wall_seconds'] * 1000, SLOTS[4])])],
                             ' ms', 'Oximut phase medians at both worker counts next to Stryker wall medians'))
    body.append(legend([(n, col) for n, _, col in segs(ph)] + [('Stryker total', SLOTS[4])]))
    body.append('<h3>Inside one run: the controller timeline</h3>')
    runs = a['runs']
    jobs_ms = {n: sum(j['_wall'] for j in js) for n, js in
               (('baseline', a['baselines'][:1]), ('instr', a['baselines'][1:]), ('mutants', a['mutants']))}
    rest = max(a['wall'] - c['controller_exit_ms'], 0)
    anatomy = [('Controller boot + initialize', a['boot_ms'], SLOTS[4]),
               ('Original baseline job', jobs_ms['baseline'], SLOTS[0]),
               ('Instrumented baseline job', jobs_ms['instr'], SLOTS[1]),
               (f'{len(a["mutants"])} mutant batches', jobs_ms['mutants'], SLOTS[2]),
               ('Gaps between jobs + close', c['controller_exit_ms'] - a['boot_ms'] - sum(jobs_ms.values()), SLOTS[3]),
               ('Rust before spawn / after exit', rest, SLOTS[5])]
    body.append(f'<p>One diagnostic run of {a["wall"]:.0f} ms at {w} workers. Segments sum to the run; the controller '
                'segments come from timestamps inside the controller process.</p>')
    body.append(stacked_bars([('One run', anatomy)], ' ms', f'One diagnostic run at {w} workers by controller segment'))
    body.append(legend([(n, col) for n, _, col in anatomy]))
    rows = []
    for job in runs:
        rows.append((job['_label'], len(job['forks']), f'{job["_t0"] - job["start"]:.0f}',
                     *(f'{statistics.median(fork_step(f, s) for f in job["forks"]):.0f}' for s in STEPS),
                     f'{job["end"] - max(f["exit"] for f in job["forks"]):.0f}', f'{job["_wall"]:.0f}',
                     f'{job["controller_cpu_ms"]:.0f}'))
    body.append(table(['Job', 'forks', 'request → first fork', 'bootstrap', 'fetch + collect', 'run tests', 'stop → exit',
                       'last exit → reply', 'job wall', 'controller CPU'], rows,
                      'Per job, ms: median fork step durations and controller-visible stages (one capture)'))
    first, later = a['mutants'][0], a['mutants'][1:]
    compare = (f' against {later[0]["_wall"]:.0f} ms and {later[0]["controller_cpu_ms"]:.0f} ms for the next'
               if later else '')
    body.append(f'<p><b>The first mutant batch:</b> {first["_wall"]:.0f} ms and '
                f'{first["controller_cpu_ms"]:.0f} ms of controller CPU{compare}. '
                f'All {d["concurrency"]} lanes share one controller process, '
                'whose main thread serves every fork\'s module requests (CPU here is process-wide, all threads). The first '
                'batch plausibly pays for cold Vite transforms in each lane project: the CPU profile makes transform/resolve '
                'the largest controller category, but only a per-batch profile would prove it. After that, a batch is '
                'dominated by fork bootstrap and the tests themselves.</p>')
    body.append(f'<p>Gaps between controller jobs total {a["gap_ms"]:.0f} ms: ' +
                ', '.join(f'{esc(n)} {g:.0f} ms' for n, g in a['gaps']) + '. See the '
                '<a href="timeline.html">fork timeline</a> for every fork.</p>')
    body.append('<h2>3. Ranked bottlenecks and estimated savings</h2>')
    body.append(f'<p>Upper bounds in capture milliseconds, from the capture\'s own timestamps. The campaign median is '
                f'{wall_ms:.0f} ms, so the gap to the 0.50× target is {gap:.0f} ms. <b>The savings overlap</b> '
                '(several share the same controller CPU) and are not additive; none is a measured result. Each '
                'would be its own PR with a paired native benchmark.</p>')
    body.append(stacked_bars([(f'#{i}', [(name, ms, SLOTS[(i - 1) % 6])]) for i, (name, ms, _) in
                              enumerate(a['estimates'], 1)], ' ms',
                             'Estimated upper bound on wall savings per bottleneck'))
    for i, (name, ms, why) in enumerate(a['estimates'], 1):
        body.append(f'<div class="rank"><h3>#{i} {esc(name)}: up to {ms:.0f} ms '
                    f'({100 * ms / a["wall"]:.0f}% of the capture)</h3><p>{esc(why)}</p>'
                    f'<p class="note">{esc(OWNERS.get(name, ""))}</p></div>')
    body.append(controller_cpu_section(d))
    body.append('<h2>Evidence and limits</h2><ul>' + ''.join(f'<li>{x}</li>' for x in [
        f'Paired campaigns: runs {esc(d["runs"]["benchmark"])} ({w} workers) and {esc(d["runs"]["benchmark_low"])} '
        f'({wl} workers); scored, ratios re-derived from raw rows.',
        f'Controller job timeline: run {esc(d["runs"]["controller"])} at {esc(c["identity"]["commit"][:7])} '
        f'(one overhead-bearing run at {w} workers; passed: {c["identity"]["passed"]}). The first mutant batch also '
        'carries the diagnostic\'s CPU-profile window.',
        f'Node CPU profiles: run {esc(d["runs"]["profiles"])} at {esc(p["identity"]["commit"][:7])}, main with the #43 '
        f'profiler tooling; passed: {p["identity"]["passed"]}, {len(p["roles"]["fork"])} of {p["forks_expected"]} fork '
        'profiles present (partial evidence; the capture\'s strict gate fails).',
        f'All four runs declare the same workload ({esc(b["workload_sha256"][:12])}).',
        'The two scored campaigns (and the Stryker medians in them) ran on different runner hosts; compare ratios, not '
        'absolute times, across them.',
        'Diagnostic runs land on different runner hosts and carry clock or profiler overhead. Durations are comparable '
        'only inside one capture.',
        'CPU profiles sample each process\'s main thread only; Stryker leaves no profile.',
    ]) + '</ul>')
    body.append('<details><summary>Input files and SHA-256</summary>' +
                table(['File', 'SHA-256'], sorted(d['inputs'].items()), 'Every consumed input file') + '</details>')
    return base.page('Oximut bottleneck report: shared lanes at '
                     f'{w} workers', ''.join(body), d['generated'])


OWNERS = {
    'Fresh-fork bootstrap on the critical path':
        'Not covered by an open thread. A prewarmed spare fork (#45) was closed under the earlier CPU/RSS rule; '
        'wall time is now the only acceptance metric, so a pre-spawned idle fork pool deserves a fresh measurement.',
    'Instrumented baseline runs after the original baseline': 'Open thread: perf/instrumented-baseline-overlap.',
    'First mutant batch pays for cold lanes': 'Open thread: perf/lane-context-warmup.',
    'Controller boot before the first job':
        'Candidates: a persistent Node compile cache for the controller (V8 validates entries by content), or shipping the '
        'controller entry as plain JavaScript so Node skips type stripping.',
    'Idle lanes and gaps between mutant batches':
        'Candidate: refill a lane as soon as its mutant finishes instead of waiting for the whole batch.',
    'Teardown after the controller closes':
        'Verified descendant cleanup must stay; only its polling cadence is a candidate.',
}


def controller_cpu_section(d):
    c, p = d['controller'], d['profiles']
    ctl = collections.Counter()
    for x in p['roles']['controller']:
        ctl.update(x['categories'])
    busy = sum(v for k, v in ctl.items() if k != 'Idle (waiting)')
    transform = ctl.get('Vite transform/resolve', 0)
    spawn = sum(us for x in p['roles']['controller'] for cat, name, where, us in x['functions']
                if name == 'spawn' and where.endswith('child_process'))
    cpu_jobs = [(j['controller_cpu_ms'], j['end'] - j['start']) for j in c['jobs'] if j['forks']]
    return (f'<h3>Why the controller matters</h3><p>One controller process serves all {d["concurrency"]} lanes. In the '
            f'profiled run its main thread is busy {busy / 1e3:.0f} ms, of which Vite transform/resolve is '
            f'{share(transform, busy)}, Node bootstrap and module loading {share(ctl.get("Node bootstrap/runtime", 0) + ctl.get("Node module loading/compile", 0), busy)}, '
            f'GC {share(ctl.get("GC", 0), busy)}, and the synchronous part of spawning forks '
            f'{spawn / 1e3:.0f} ms. In the controller-job capture its CPU is '
            f'{", ".join(f"{cpu:.0f} of {wall:.0f} ms" for cpu, wall in cpu_jobs)} for the jobs in order. See '
            '<a href="cpu.html">CPU</a> for functions.</p>')


def render_timeline(d):
    c, w = d['controller'], d['concurrency']
    a = analyse(d)
    runs = a['runs']
    t0 = c['controller_start_ms']
    t1 = max(f['exit'] for j in runs for f in j['forks'])
    width, label_w, row = 1000, 200, 22
    plot = width - label_w - 20
    sx = lambda t: label_w + plot * (t - t0) / (t1 - t0)
    forks = []
    for job in runs:
        name = job['_label']
        for i, fork in enumerate(sorted(job['forks'], key=lambda f: f['fork']), 1):
            forks.append((job, f'{name} · fork {i}', fork))
    height = len(forks) * row + 60
    colors = [(n, *STEPS[n], SLOTS[i]) for i, n in enumerate(STEPS)]
    label = f'Gantt chart: lifecycle of each of the {len(forks)} Vitest forks in one run at {w} workers'
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(label)}">']
    for tick in range(0, int(t1 - t0) + 1, 250):
        x = sx(t0 + tick)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 40}" stroke="var(--grid)"/>'
                   f'<text x="{x:.1f}" y="{height - 24}" text-anchor="middle">{tick} ms</text>')
    boot = t0 + a['boot_ms']
    out.append(f'<rect x="{sx(t0):.1f}" y="2" width="{sx(boot) - sx(t0):.1f}" height="{height - 46}" fill="var(--s5)" '
               f'opacity="0.18"><title>Controller boot + initialize ({a["boot_ms"]:.0f} ms)</title></rect>')
    for i, job in enumerate(runs):
        out.append(f'<rect x="{sx(job["start"]):.1f}" y="2" width="{sx(job["end"]) - sx(job["start"]):.1f}" '
                   f'height="{height - 46}" fill="var(--panel)" opacity="{0.9 if i % 2 else 0.5}">'
                   f'<title>{esc(job["_label"])}: request → reply {job["_wall"]:.0f} ms</title></rect>')
    for i, (job, name, fork) in enumerate(forks):
        y = i * row + 4
        out.append(f'<text class="lbl" x="{label_w - 8}" y="{y + 13}" text-anchor="end">{esc(name)}</text>')
        for step, s0, s1, color in colors:
            x0, x1 = sx(fork[s0]), sx(fork[s1])
            out.append(f'<rect x="{x0:.1f}" y="{y}" width="{max(x1 - x0 - 1, 0.6):.1f}" height="{row - 6}" rx="3" '
                       f'fill="{color}"><title>{esc(name)} - {esc(step)}: {fork[s1] - fork[s0]:.1f} ms</title></rect>')
    out.append('</svg>')
    fork_rows = [(name, f'{fork["fork"] - t0:.1f}') + tuple(f'{fork_step(fork, s):.1f}' for s in STEPS)
                 for _, name, fork in forks]
    seg = collections.defaultdict(list)
    for job, _, fork in forks:
        kind = 'mutant' if job['kind'] == 'mutant' else 'baseline'
        for s in STEPS:
            seg[(kind, s)].append(fork_step(fork, s))
    step_rows = [(f'{kind}: {s}', f'{statistics.median(v):.1f}', f'{min(v):.1f}', f'{max(v):.1f}', len(v))
                 for (kind, s), v in sorted(seg.items())]
    job_rows = []
    for job in runs:
        job_rows.append((job['_label'], len(job['forks']), f'{job["_wall"]:.0f}', f'{job["controller_cpu_ms"]:.0f}',
                         share(job['controller_cpu_ms'], job['_wall'])))
    bars = [(j['_label'], [('Controller CPU', min(j['controller_cpu_ms'], j['_wall']), SLOTS[1]),
                                          ('Wall beyond controller CPU', max(j['_wall'] - j['controller_cpu_ms'], 0), SLOTS[0])])
            for j in runs]
    recon = [('originalBaseline', c['phases']['originalBaseline'],
              a['boot_ms'] + a['baselines'][0]['_wall'], 'controller boot + first baseline job'),
             ('instrumentedBaseline', c['phases']['instrumentedBaseline'], a['baselines'][1]['_wall'],
              'second baseline job'),
             ('mutationExecution', c['phases']['mutationExecution'],
              a['mutants'][-1]['end'] - a['mutants'][0]['start'], f'first request → last reply of {len(a["mutants"])} batches'),
             ('teardown', c['phases']['teardown'], next(j for j in c['jobs'] if j['command'] == 'close')['end'] -
              next(j for j in c['jobs'] if j['command'] == 'close')['start'], 'controller close request')]
    host = c['tools']['stryker']['wall_seconds'] / d['benchmark']['medians']['after/stryker']['wall_seconds']
    body = [f'<p><b>One diagnostic run</b> from the controller-job capture at {w} workers. In it, Stryker took '
            f'{100 * host:.0f}% of the scored campaign\'s median Stryker wall time (same Stryker code; one unwarmed run '
            'versus a warmed median). Absolute milliseconds are valid only within this run. Each row is one fresh '
            'Vitest fork, coloured by lifecycle step; alternating bands are controller jobs (a mutant batch runs one '
            'fork per lane); the shaded left band is controller boot. Blank space between bands is time between jobs.</p>',
            out[0] + ''.join(out[1:]),
            legend([(n, col) for n, _, _, col in colors] + [('Controller boot', 'var(--s5)')]),
            '<details><summary>Data table</summary>' +
            table(['Fork', 'start ms'] + list(STEPS), fork_rows, f'Data for: {label}') + '</details>',
            '<h2>Controller CPU against job wall</h2>',
            '<p>Controller-process CPU (all threads) against each job\'s wall time. A job whose controller CPU approaches its '
            'wall time keeps the controller process busy throughout, which makes the controller a suspect rather '
            'than proof of a bottleneck.</p>',
            stacked_bars(bars, ' ms', 'Controller CPU against job wall for each controller job'),
            legend([('Controller CPU', SLOTS[1]), ('Wall beyond controller CPU', SLOTS[0])]),
            table(['Job', 'forks', 'wall ms', 'controller CPU ms', 'CPU / wall'], job_rows,
                  'Controller CPU (all threads of the process) against controller-visible job wall'),
            '<h2>Gaps between controller jobs</h2>',
            table(['Between', 'ms'], [(n, f'{g:.1f}') for n, g in a['gaps']],
                  f'Reply of one job to the next request: {a["gap_ms"]:.0f} ms in total (Rust, IPC, scheduling)'),
            '<h2>Per-step durations (ms)</h2>',
            table(['Step', 'median', 'min', 'max', 'n'], step_rows, 'Per-fork step durations within this run'),
            '<h2>Rust phases vs controller-visible time (same run)</h2>',
            table(['Rust phase', 'Rust wall ms', 'controller-visible ms', 'controller-visible part'],
                  [(n, f'{x:.0f}', f'{y:.0f}', z) for n, x, y, z in recon],
                  'The difference is Rust CLI, slot process, IPC and scheduling outside the controller'),
            f'<p class="note">Run {esc(d["runs"]["controller"])}. The first mutant batch also carries the controller '
            'CPU-profile window of this diagnostic mode, which adds some overhead to that batch.</p>']
    return base.page('Oximut job timeline: shared lanes', ''.join(body), d['generated'])


def render_cpu(d):
    p, c = d['profiles'], d['controller']
    order = ['colord source + tests', 'Vitest runtime (runner/expect/chai)', 'Node module loading/compile',
             'Node bootstrap/runtime', 'GC', 'Vite transform/resolve', 'V8/native runtime',
             'Oximut adapter', 'Other dependencies', 'Idle (waiting)']
    color = dict(zip(order, SLOTS + ['var(--muted)'] * 4, strict=False))

    def totals(role):
        acc = collections.Counter()
        for x in p['roles'][role]:
            acc.update(x['categories'])
        return acc
    fork_t, ctl_t = totals('fork'), totals('controller')
    main6 = order[:6]

    def segs(acc):
        out = [(k, acc.get(k, 0) / 1e3, color[k]) for k in main6]
        out.append(('Everything else (busy)',
                    sum(v for k, v in acc.items() if k not in main6 and k != 'Idle (waiting)') / 1e3, 'var(--muted)'))
        return out
    present, expected = len(p['roles']['fork']), p['forks_expected']
    body = [f'<p>Sampled Node CPU profiles from run {esc(d["runs"]["profiles"])} at {esc(p["identity"]["commit"][:7])} '
            f'(main with the #43 profiler tooling; {d["concurrency"]} workers; main thread only; profiler overhead '
            'included). Categories group self time by where the code lives. Idle time is excluded from the bars and '
            'shown in the table.</p>']
    if present != expected:
        body.append(f'<p><b>Partial evidence:</b> {present} of {expected} fork profiles were captured, so fork totals '
                    'cover only the captured forks. Shares within them remain comparable.</p>')
    body.append(stacked_bars([(f'{present} forks (sum)', segs(fork_t)), ('Controller', segs(ctl_t))], ' ms',
                             'Busy Node CPU by category: forks versus controller'))
    body.append(legend([(k, color[k]) for k in main6] + [('Everything else (busy)', 'var(--muted)')]))
    fb = sum(v for k, v in fork_t.items() if k != 'Idle (waiting)')
    cb = sum(v for k, v in ctl_t.items() if k != 'Idle (waiting)')
    body.append(table(['Category', 'forks ms', 'share of busy', 'controller ms', 'share of busy'], [
        (k, f'{fork_t.get(k, 0) / 1e3:.1f}', '-' if k.startswith('Idle') else share(fork_t.get(k, 0), fb),
         f'{ctl_t.get(k, 0) / 1e3:.1f}', '-' if k.startswith('Idle') else share(ctl_t.get(k, 0), cb))
        for k in order], 'All categories, including idle time'))
    startup = sum(fork_t.get(k, 0) for k in ('Node bootstrap/runtime', 'Node module loading/compile', 'GC',
                                             'V8/native runtime'))
    body.append(f'<p>Inside forks, Node bootstrap, module loading/compile, GC and V8 internals take {share(startup, fb)} '
                f'of busy time and the colord sources and tests only {share(fork_t.get("colord source + tests", 0), fb)}: '
                'the work every fresh process repeats dwarfs the work the mutant needs. In the controller, '
                f'Vite transform/resolve alone is {share(ctl_t.get("Vite transform/resolve", 0), cb)} of busy time.</p>')
    for role in ('fork', 'controller'):
        fn = collections.Counter()
        for x in p['roles'][role]:
            for cat, name, where, us in x['functions']:
                if cat != 'Idle (waiting)':
                    fn[(cat, name, where)] += us
        body.append(f'<h3>Top self-time functions: {"forks" if role == "fork" else "controller"}</h3>')
        body.append(table(['Function', 'Where', 'Category', 'ms'],
                          [(n, wh, cat, f'{us / 1e3:.1f}') for (cat, n, wh), us in fn.most_common(15)],
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
    ox, sk = d['benchmark']['medians']['after/oximut'], d['benchmark']['medians']['after/stryker']
    body.append('<h2>Process tree, scored campaign medians</h2>')
    body.append(table(['Tool', 'CPU s', 'user s', 'sys s', 'Peak RSS MB', 'Peak / observed processes'], [
        (n, f'{x["cpu_seconds"]:.2f}', f'{x["cpu_user_seconds"]:.2f}', f'{x["cpu_system_seconds"]:.2f}',
         f'{x["peak_aggregate_rss_bytes"] / 1e6:.0f}', f'{x["peak_process_count"]:.0f} / {x["observed_process_count"]:.0f}')
        for n, x in (('Oximut', ox), ('Stryker', sk))], f'Whole-tree totals at {d["concurrency"]} workers'))
    body.append(f'<p class="note">Profiles: run {esc(d["runs"]["profiles"])}. Controller self CPU in the un-profiled '
                f'controller capture: {c["controller_self_cpu_ms"]["user"]:.0f} ms user / '
                f'{c["controller_self_cpu_ms"]["system"]:.0f} ms sys.</p>')
    return base.page('Oximut CPU: controller and forks at '
                     f'{d["concurrency"]} workers', ''.join(body), d['generated'])


def render_all(data):
    return {'index.html': render_overview(data), 'timeline.html': render_timeline(data),
            'cpu.html': render_cpu(data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='stage', required=True)
    ex = sub.add_parser('extract')
    for name in ('benchmark', 'benchmark-low', 'controller', 'profiles'):
        ex.add_argument(f'--{name}', required=True, type=Path)
        ex.add_argument(f'--{name}-run', required=True)
    ex.add_argument('--generated', required=True, help='provenance line shown on every page')
    ex.add_argument('--output', required=True, type=Path)
    rd = sub.add_parser('render')
    rd.add_argument('--data', required=True, type=Path)
    rd.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.stage == 'extract':
        base.write_atomic(args.output, json.dumps(extract(args), indent=1, sort_keys=True) + '\n')
    else:
        data = json.loads(args.data.read_text(encoding='utf-8'))
        if data.get('schema') != SCHEMA:
            raise SystemExit(f'unsupported data schema {data.get("schema")}; expected {SCHEMA}')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        # Render every page before replacing any, so a failure leaves the previous set intact.
        for name, text in render_all(data).items():
            base.write_atomic(args.output_dir / name, text)


if __name__ == '__main__':
    main()
