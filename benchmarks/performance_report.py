#!/usr/bin/env python3
"""Render qualified native evidence as one offline HTML page; run no benchmarks."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import html
import json
import math
from pathlib import Path
import statistics

import profile_native
import compare_candidates

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('wall_seconds', 'cpu_seconds', 'cpu_user_seconds', 'cpu_system_seconds',
          'peak_aggregate_rss_bytes', 'peak_process_count', 'observed_process_count')
TARGETS = {'wall_seconds': .5, 'cpu_seconds': .8, 'peak_aggregate_rss_bytes': .8}
LABELS = {'wall_seconds': 'Wall', 'cpu_seconds': 'CPU', 'peak_aggregate_rss_bytes': 'RSS'}


def require(condition, message):
    """Reject evidence that cannot support a reported value."""
    if not condition:
        raise ValueError(message)


def read_json(path):
    """Load one retained JSON input without modifying its bytes."""
    return json.loads(path.read_text())


def verify_manifest(root, required):
    """Verify retained bytes before deriving anything for display."""
    hashes = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(name not in hashes, 'duplicate evidence manifest entry')
        path = root / name
        require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()), 'unsafe evidence path')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'evidence digest mismatch: {name}')
        hashes[name] = digest
    require(bool(hashes), 'empty evidence manifest')
    require(set(required) <= hashes.keys(), 'unhashed evidence: '+', '.join(sorted(set(required)-hashes.keys())))
    return hashes


def collect(comparison, controller, accounting=None):
    """Keep paired controls and overhead-bearing diagnostics in separate scopes."""
    hashes = verify_manifest(comparison, ('original-summary.json','original-metadata.json',
                                         'original-measurements.jsonl','evidence-source.json'))
    summary = read_json(comparison / 'original-summary.json')
    metadata = read_json(comparison / 'original-metadata.json')
    source = read_json(comparison / 'evidence-source.json')
    require(all(type(source[key]) is int and source[key] > 0 for key in ('benchmark_run','structural_run','controller_run')), 'invalid native run provenance')
    require(summary['correctness_gate']['passed'] is True and summary['build_integrity']['passed'] is True,
            'qualified workload and strict build integrity required')
    require(summary['measured_pairs'] >= 5 and summary['warmup_pairs'] >= 1, 'at least one warmup and five pairs required')
    raw = [json.loads(line) for line in (comparison / 'original-measurements.jsonl').read_text().splitlines()]
    groups, seen = defaultdict(list), set()
    for row in raw:
        side = Path(row['subcampaign']).name.rsplit('-', 1)[-1]
        require(side in ('after', 'before') and row['tool'] in ('oximut','stryker','vitest'), 'unexpected measurement group')
        key = (row['campaign_round'], side, row['tool'])
        require(type(row['campaign_round']) is int and row['warmup'] == (row['campaign_round'] < summary['warmup_pairs']), 'invalid paired round classification')
        require(key not in seen, 'duplicate measurement')
        seen.add(key)
        require(row['success'] is True and not row['timed_out'] and not row.get('memory_errors'), 'failed measurement')
        require(row['memory_method'] == 'sum-live-smaps_rollup-rss' and row['exit_code'] == 0, 'unqualified resource observation')
        require(type(row['warmup']) is bool, 'invalid warmup classification')
        for field in FIELDS:
            require(type(row[field]) in (int, float) and math.isfinite(row[field]) and row[field] >= 0, 'invalid resource metric')
        require(row['wall_seconds'] > 0 and row['cpu_seconds'] > 0 and row['peak_aggregate_rss_bytes'] > 0, 'empty resource metric')
        require(all(type(row[k]) is int and row[k] > 0 for k in ('peak_process_count','observed_process_count'))
                and row['observed_process_count'] >= row['peak_process_count'], 'invalid process counts')
        require(math.isclose(row['cpu_seconds'], row['cpu_user_seconds'] + row['cpu_system_seconds'], abs_tol=1e-8), 'CPU components differ')
        if not row['warmup']:
            groups[side, row['tool']].append(row)
    expected = {(r, side, tool) for r in range(summary['warmup_pairs'] + summary['measured_pairs'])
                for side in ('after','before') for tool in ('oximut','stryker','vitest')}
    require(seen == expected, 'measurement inventory differs from paired campaign')
    provenance = {side: groups[side,'oximut'][0]['execution_inputs'] for side in ('after','before')}
    qualified = compare_candidates.evaluate(raw, summary['warmup_pairs'], summary['measured_pairs'], provenance)
    require(qualified['correctness_gate']['passed'], 'raw workload requalification failed')
    medians = {}
    for (side, tool), rows in groups.items():
        require(len(rows) == summary['measured_pairs'], 'incomplete measured group')
        medians[f'{side}/{tool}'] = {field: statistics.median(row[field] for row in rows) for field in FIELDS}
    for name, (left, right) in {'after_vs_stryker': (('after','oximut'),('after','stryker')),
                                'before_vs_stryker': (('before','oximut'),('before','stryker')),
                                'after_vs_before': (('after','oximut'),('before','oximut'))}.items():
        for field in TARGETS:
            pairs = []
            a = {r['campaign_round']: r[field] for r in groups[left]}
            b = {r['campaign_round']: r[field] for r in groups[right]}
            for index in sorted(a):
                pairs.append(a[index] / b[index])
            stated = summary['comparisons'][name]['metrics'][field]
            require(len(stated['paired_ratios']) == len(pairs) and all(math.isclose(x,y) for x,y in zip(pairs,stated['paired_ratios'])), 'paired ratios differ from raw rows')
            lo, hi = stated['bootstrap_95_percent']
            require(all(type(x) in (int,float) and math.isfinite(x) for x in (lo, hi, stated['median']))
                    and 0 <= lo <= stated['median'] <= hi and math.isclose(statistics.median(pairs),stated['median']), 'invalid paired interval')
            recomputed = qualified['comparisons'][name]['metrics'][field]['bootstrap_95_percent']
            require(all(math.isclose(x,y,rel_tol=1e-12,abs_tol=1e-12) for x,y in zip((lo,hi),recomputed)),
                    'bootstrap bounds differ from raw paired evidence')
    phases = {}
    for side in ('after','before'):
        rows = [row for row in summary['phase_evidence'][side] if not row['warmup']]
        require(len(rows) == summary['measured_pairs'], 'incomplete phase evidence')
        require(sorted(row['round'] for row in rows) == list(range(summary['warmup_pairs'], summary['warmup_pairs']+summary['measured_pairs'])), 'phase round inventory differs')
        for row in rows:
            require(row['phaseWallMs'].keys() == rows[0]['phaseWallMs'].keys() and all(
                type(v) in (int,float) and math.isfinite(v) and v >= 0 for v in row['phaseWallMs'].values()), 'invalid phase observation')
        phases[side] = {key: statistics.median(row['phaseWallMs'][key] for row in rows) for key in rows[0]['phaseWallMs']}
        require(all(math.isfinite(v) and v >= 0 for v in phases[side].values()), 'invalid phase timing')
    verify_manifest(controller, ['boundary-intervals.json','qualification/summary.json',
                                'qualification/measurements.jsonl','qualification/00-oximut/report.json']+
                    [str(p.relative_to(controller)) for p in (controller/'profiles').iterdir() if p.is_file()])
    require(not profile_native.qualification_errors(controller / 'qualification', 0), 'controller workload not qualified')
    capture = profile_native.controller_capture_inventory(controller / 'profiles', controller / 'qualification')
    require(not capture['errors'] and capture['workload_reconciled'], 'controller capture invalid')
    cpu_files = list((controller / 'profiles').glob('*.cpuprofile'))
    require(len(cpu_files) == 1, 'one declared first-mutant controller profile required')
    require(hashlib.sha256(cpu_files[0].read_bytes()).hexdigest() == source['controller_profile_sha256'], 'controller capture differs from declared provenance')
    cpu = read_json(cpu_files[0])
    require(profile_native.node_profile_finalized(cpu), 'invalid CPU profile')
    frames = {node['id']: node['callFrame'] for node in cpu['nodes']}
    weights = defaultdict(float)
    for sample, delta in zip(cpu['samples'], cpu['timeDeltas']):
        frame = frames[sample]
        weights[(frame['functionName'] or '(anonymous)', frame['url'])] += delta / 1000
    profile_rows = [{'function': key[0], 'file': key[1], 'sampled_ms': value}
                    for key,value in sorted(weights.items(), key=lambda item: -item[1])[:12]]
    accounting_data = None
    accounting_run = None
    if accounting:
        verify_manifest(accounting, ('diagnostic.json','build-metadata.json','qualification/metadata.json',
                                     'qualification/summary.json','qualification/measurements.jsonl',
                                     'qualification/00-oximut/report.json','evidence-source.json'))
        record = read_json(accounting / 'diagnostic.json')
        require(record.get('passed') is True and record.get('node_capture_mode') == 'rust-accounting-self', 'accounting capture incomplete or wrong mode')
        build = read_json(accounting/'build-metadata.json')
        capture_metadata = read_json(accounting/'qualification/metadata.json')
        require(record['source_commit'] == build['commit'] == metadata['after_commit'], 'accounting source differs from paired candidate')
        require(record['execution_inputs'] == capture_metadata['execution_inputs'], 'accounting execution provenance differs')
        require(record['execution_inputs']['binary_sha256'] == build['binary_sha256'] == metadata['binary_sha256']['after'], 'accounting binary differs from paired candidate')
        for key in ('binary_sha256','adapter_source_sha256','adapter_pairing','oximut_args'):
            require(record['execution_inputs'][key] == provenance['after'][key], 'accounting execution inputs differ from paired candidate')
        require(not profile_native.qualification_errors(accounting / 'qualification', 0), 'accounting workload not qualified')
        clock_version = record.get('accounting_clock_version',1)
        require(type(clock_version) is int and clock_version in (1,2), 'invalid accounting clock version')
        inventory = profile_native.accounting_capture_inventory(accounting / 'qualification',allow_legacy=clock_version==1)
        require(not inventory['errors'] and inventory['clock_version']==clock_version, 'accounting evidence incomplete')
        accounting_data = inventory['accounting_diagnostics']
        accounting_run = {'rows': [json.loads(line) for line in (accounting/'qualification/measurements.jsonl').read_text().splitlines()],
                          'phases': read_json(accounting/'qualification/00-oximut/report.json')['resources']['phaseWallMs'],
                          'source_commit': record['source_commit']}
        if (accounting/'evidence-source.json').exists():
            provenance=read_json(accounting/'evidence-source.json')
            require(type(provenance['run']) is int and provenance['run'] > 0, 'invalid accounting run provenance')
            accounting_run['native_run']=provenance['run']
    return {'medians': medians, 'phases': phases, 'comparisons': summary['comparisons'],
            'pairs': summary['measured_pairs'], 'warmups': summary['warmup_pairs'], 'build': metadata,
            'hashes': hashes, 'source': source, 'raw': [{k: row[k] for k in (*FIELDS,'tool','campaign_round','warmup')} |
                                     {'side': Path(row['subcampaign']).name.rsplit('-',1)[-1]} for row in raw],
            'controller': {'intervals': read_json(controller / 'boundary-intervals.json'),
                           'frames': profile_rows, 'sampled_window_ms': (cpu['endTime']-cpu['startTime'])/1000,
                           'rows': [json.loads(line) for line in (controller/'qualification/measurements.jsonl').read_text().splitlines()],
                           'phases': read_json(controller/'qualification/00-oximut/report.json')['resources']['phaseWallMs']},
            'accounting': accounting_data, 'accounting_run': accounting_run}


def table(headers, rows, caption=None):
    """Render escaped tabular values with an optional accessible caption."""
    def cells(values, tag):
        """Escape each cell value before inserting it into table markup."""
        return ''.join(f'<{tag}>{html.escape(str(value))}</{tag}>' for value in values)
    caption_html='<caption>'+html.escape(caption)+'</caption>' if caption else ''
    return '<div class="scroll"><table>'+caption_html+'<thead><tr>'+cells(headers,'th')+'</tr></thead><tbody>'+''.join('<tr>'+cells(row,'td')+'</tr>' for row in rows)+'</tbody></table></div>'


def bars(values, label='Milliseconds'):
    """Draw labeled wall-time intervals without implying an additive partition."""
    maximum = max(value for _,value in values) or 1
    height = len(values)*35+20
    svg = [f'<svg viewBox="0 0 720 {height}" role="img" aria-label="{html.escape(label)}" style="height:{height}px">']
    for index,(name,value) in enumerate(values):
        y = 20+index*35
        svg.append(f'<text x="0" y="{y}" class="axis">{html.escape(name)}</text><rect x="250" y="{y-12}" width="{360*value/maximum:.3f}" height="17" rx="3" class="bar"/><text x="620" y="{y}" class="axis">{value:.3f} ms</text>')
    return '<div class="scroll">'+''.join(svg)+'</svg></div>'


def ratio_chart(data, key):
    """Expose paired ratios and confidence bounds as a chart and a table."""
    values = data['comparisons'][key]['metrics']
    title={'after_vs_stryker':'After / Stryker','after_vs_before':'After / before',
           'before_vs_stryker':'Before / Stryker'}[key]
    maximum = max(1.5, max(values[field]['bootstrap_95_percent'][1] for field in TARGETS)*1.1)
    x = lambda value: 120+value/maximum*390
    out = ['<svg viewBox="0 0 720 185" role="img" aria-label="'+html.escape(title)+' paired ratios with bootstrap 95 percent intervals" style="height:185px">']
    for index,field in enumerate(TARGETS):
        metric = values[field]; lo,hi = metric['bootstrap_95_percent']; y = 35+index*50
        target = 1 if key == 'after_vs_before' else TARGETS[field]
        out.append(f'<text x="0" y="{y+4}" class="axis">{LABELS[field]}</text><line x1="{x(1):.2f}" x2="{x(1):.2f}" y1="{y-17}" y2="{y+17}" class="parity"/><line x1="{x(target):.2f}" x2="{x(target):.2f}" y1="{y-17}" y2="{y+17}" class="target"/><line x1="{x(lo):.2f}" x2="{x(hi):.2f}" y1="{y}" y2="{y}" class="interval"/><circle cx="{x(metric["median"]):.2f}" cy="{y}" r="5" class="point"/><text x="530" y="{y+4}" class="axis">{metric["median"]:.3f} [{lo:.3f}, {hi:.3f}]</text>')
    out.append('<text x="120" y="180" class="axis">0 → lower is better • gray: parity • green: target/parity</text>')
    rows=[[LABELS[field],f'{values[field]["median"]:.3f}',
           f'{values[field]["bootstrap_95_percent"][0]:.3f}',
           f'{values[field]["bootstrap_95_percent"][1]:.3f}'] for field in TARGETS]
    return '<div class="scroll">'+''.join(out)+'</svg></div>'+table(
        ['Metric','Median ratio','95% CI lower','95% CI upper'],rows,caption=title)


def render(data):
    """Render escaped labels and inline SVG, with no third-party runtime assets."""
    metrics = []
    for side in ('after','before'):
        for tool in ('oximut','stryker','vitest'):
            d = data['medians'][f'{side}/{tool}']
            metrics.append([side,tool,f'{d["wall_seconds"]:.3f}',f'{d["cpu_seconds"]:.3f}',f'{d["cpu_user_seconds"]:.3f} / {d["cpu_system_seconds"]:.3f}',f'{d["peak_aggregate_rss_bytes"]/1e6:.2f}',f'{d["peak_process_count"]:g} / {d["observed_process_count"]:g}'])
    target_rows = []
    for field,target in TARGETS.items():
        d=data['comparisons']['after_vs_stryker']['metrics'][field]; lo,hi=d['bootstrap_95_percent']
        target_rows.append([LABELS[field],f'≤ {target:.2f}',f'{d["median"]:.3f} [{lo:.3f}, {hi:.3f}]','PASS' if hi <= target else 'MISS'])
    phases = data['phases']['after']
    intervals = data['controller']['intervals']
    interval_keys = [('forkToStartedMs','Fork → started'),('runToCollectionMs','Run → collected'),('collectionToFinishedMs','Collected → finished'),('requestToSendReturnMs','Request → local send return')]
    boundaries = [(label,statistics.median(job[key] for job in intervals['jobs'])) for key,label in interval_keys]
    accounting = '<p class="notice">Pending native capture. No zero-cost or hotspot attribution is inferred from this gap.</p>'
    def diagnostic_resources(run):
        """Keep one profiled run separate from paired performance comparisons."""
        return table(['Tool','Wall s','CPU s','User / sys s','RSS MB','Peak / observed'], [[r['tool'],f'{r["wall_seconds"]:.6f}',f'{r["cpu_seconds"]:.6f}',f'{r["cpu_user_seconds"]:.6f} / {r["cpu_system_seconds"]:.6f}',f'{r["peak_aggregate_rss_bytes"]/1e6:.3f}',f'{r["peak_process_count"]} / {r["observed_process_count"]}'] for r in run['rows']])+table(['Diagnostic resources.phaseWallMs','ms'],[[key,f'{value:.6f}'] for key,value in run['phases'].items()])+'<p>One overhead-bearing run: paired ratios and bootstrap CIs are unavailable.</p>'
    if data['accounting']:
        precise = data['accounting'].get('clockVersion') == 2
        accounting = table(['Nested stage','Calls','Wall ms','Precise self CPU ms','Raw user ms','Raw sys ms'], [[key,d['calls'],f'{d["wallNs"]/1e6:.3f}',f'{d["selfCpuNs"]/1e6:.3f}' if precise else 'N/A (legacy)',f'{d["selfUserNs"]/1e6:.3f}',f'{d["selfSystemNs"]/1e6:.3f}'] for key,d in data['accounting'].items() if isinstance(d,dict)])
        if precise:
            total=data['accounting']['threadCpuSinceBudgetStartNs']
            windows=data['accounting']['sampling']['selfCpuNs']
            accounting += table(['CLI sampling thread interval','CPU ms'],[
                ['Budget intervals through resource snapshots',f'{total/1e6:.3f}'],
                ['Sampling windows',f'{windows/1e6:.3f}'],
                ['Outside sampling, within Budget intervals',f'{(total-windows)/1e6:.3f}']])
            accounting += '<p>Version 2 uses CLOCK_THREAD_CPUTIME_ID with CPU endpoints inside wall endpoints. Budget intervals cover the same thread through the resource snapshot; they exclude earlier startup, other processes and gaps between retry intervals. The remainder above is unwindowed CLI work, not worker CPU.</p>'
        else:
            accounting += '<p class="notice">Legacy capture: precise thread CPU was not recorded. A version 2 native capture is required before interpreting exact sampling CPU totals.</p>'
        if data['accounting_run'].get('native_run'):
            run=data['accounting_run']['native_run']
            accounting='<p>Separate native accounting diagnostic <a href="https://github.com/RuiCostafrg/oximut/actions/runs/'+str(run)+'">'+str(run)+'</a>, with profiler overhead and no scored ratios.</p>'+accounting
        accounting += '<details><summary>Accounting capture resources and phases</summary>'+diagnostic_resources(data['accounting_run'])+'</details>'
        skewed=[key for key,d in data['accounting'].items() if isinstance(d,dict)
                and d['selfUserNs']+d['selfSystemNs'] > d['wallNs']]
        if skewed:
            accounting += '<p class="notice">Clock-boundary limitation observed in '+html.escape(', '.join(skewed))+': reported user + sys exceeds measured wall time. These fine-grained getrusage splits are not reliable exact stage attribution. Raw counters are retained; do not use them for CPU-share percentages. The cause is not established by these counters.</p>'
        for row in data['accounting_run']['rows']:
            snapshot=row['peak_rss_snapshot']
            accounting += '<h3>'+html.escape(row['tool'])+' peak RSS sample</h3>'+table(
                ['PID','Parent PID','Command name','Start ticks','RSS MB'],
                [[p['pid'],p['ppid'],p['comm'],p['starttime'],f'{p["rss_bytes"]/1e6:.3f}']
                 for p in sorted(snapshot['processes'],key=lambda p:-p['rss_bytes'])])
        accounting += '<p class="muted">Process RSS values come from the same verified peak sample, not independent per-process peaks. Shared pages count per process; sampling across PIDs is not atomic. No command-line arguments or environment values are collected.</p>'
    accounting_note = ('Measured stage totals cover only sampling; Budget totals, when present, bound a larger CLI thread interval without partitioning its remainder. No per-process worker CPU attribution is established. A saving still requires a paired optimization campaign.' if data['accounting'] else 'Repeated smaps_rollup reads are a source-supported cost hypothesis. Do not label them a measured CPU hotspot until the native capture arrives.')
    frames=table(['Controller sampled frame','File','Weighted sample ms'], [[r['function'],r['file'].rsplit('/',1)[-1],f'{r["sampled_ms"]:.3f}'] for r in data['controller']['frames']])
    raw_table=table(['Pair','Side','Tool','Warmup','Wall s','CPU s','User / sys s','RSS MB','Peak / observed'],[[r['campaign_round'],r['side'],r['tool'],r['warmup'],f'{r["wall_seconds"]:.6f}',f'{r["cpu_seconds"]:.6f}',f'{r["cpu_user_seconds"]:.6f} / {r["cpu_system_seconds"]:.6f}',f'{r["peak_aggregate_rss_bytes"]/1e6:.3f}',f'{r["peak_process_count"]} / {r["observed_process_count"]}'] for r in data['raw']])
    priority=('Measure forced-sample delay and worker bootstrap CPU, then choose an experiment from precise CPU totals. Preserve current RSS read freshness, admission and cleanup guarantees.' if data['accounting'] and data['accounting'].get('clockVersion')==2 else
              'Collect a version 2 native capture before attributing sampling CPU. The legacy RSS-read wall windows are measured; recoverable wall savings remain unknown. Keep current precision, read freshness, admission and cleanup guarantees.' if data['accounting'] else
              'Attribute Rust sampling CPU. Keep current RSS reads, cadence, ownership and admission/cleanup gates. Its CPU cost is still unknown.')
    unchanged=all(metric['bootstrap_95_percent'][0] <= 1 <= metric['bootstrap_95_percent'][1]
                  for metric in data['comparisons']['after_vs_before']['metrics'].values())
    control_note=('Every after/before interval includes 1. No measured speedup is established.' if unchanged else
                  'Some after/before intervals exclude 1; inspect the control change and uncertainty. This profiling PR claims no optimization.')
    content = f'''<p class="muted">Oximut · native Ubuntu 24.04 x86_64 · colord · {data['warmups']} warmup + {data['pairs']} pairs · <a href="https://github.com/RuiCostafrg/oximut/actions/runs/{data['source']['benchmark_run']}">benchmark {data['source']['benchmark_run']}</a></p>
<section><h2>Performance goal is still unmet</h2><p>Exact workload and strict build integrity pass: 41 sources, 102 tests, five mutation files, ten exact killed arithmetic edits. Fresh forks remain the default. This is a same-engine control, with no attributable optimization.</p>{table(['Metric','Required CI upper bound','Oximut / Stryker [95% CI]','Gate'],target_rows)}</section>
<section><h2>Paired ratios, not cross-run subtraction</h2><p class="muted">Median paired ratios and bootstrap 95% intervals. The mission thresholds supersede the older evaluator thresholds.</p><h3>After / Stryker</h3>{ratio_chart(data,'after_vs_stryker')}<details><summary>After / before — control</summary>{ratio_chart(data,'after_vs_before')}<p>{control_note}</p></details><details><summary>Before / Stryker</summary>{ratio_chart(data,'before_vs_stryker')}</details></section>
<section><h2>Wall, CPU, memory and processes</h2>{table(['Candidate','Tool','Wall s','CPU s','User / sys s','Aggregate RSS MB','Peak / observed processes'],metrics)}<p class="muted">Decimal MB. Each column is an independent median, so median user + median sys need not equal median total CPU. RSS is the peak sum across the owned process tree; shared pages count per process.</p></section>
<section><h2>Where wall time goes</h2>{bars([(k,phases[k]) for k in ('mutationExecution','originalBaseline','instrumentedBaseline','teardown')])}<details><summary>Every resources.phaseWallMs timing</summary>{table(['Phase','After median ms','Before median ms'],[[key,f'{value:.6f}',f'{data["phases"]["before"][key]:.6f}'] for key,value in phases.items()])}</details><p class="muted">Independent phase medians are not additive. The original baseline includes controller/Vitest initialization; mutation execution includes scheduling, sampling, workers, tests and cleanup. These are wall intervals, not CPU attribution.</p></section>
<section><h2>Inside a mutant job</h2><p class="muted">Separate overhead-bearing native diagnostic <a href="https://github.com/RuiCostafrg/oximut/actions/runs/{data['source']['controller_run']}">{data['source']['controller_run']}</a>. These observations cannot be subtracted from the paired control above.</p>{bars(boundaries)}<p>Fork startup and collection are material observed intervals. Request → send measures normal local send return, not recipient delivery. Intervals are independent medians and are not additive.</p><details><summary>All ten jobs, including profiler startup overhead</summary>{table(['Command','Request → send ms','Fork → started ms','Run → collection ms','Collection → finished ms'],[[j['commandId'],f'{j["requestToSendReturnMs"]:.3f}',f'{j["forkToStartedMs"]:.3f}',f'{j["runToCollectionMs"]:.3f}',f'{j["collectionToFinishedMs"]:.3f}'] for j in intervals['jobs']])}</details></section>
<section><h2>TypeScript / Node profiler</h2><p>V8 inspector CPU sampling covers a partial first-mutant controller window ({data['controller']['sampled_window_ms']:.3f} ms). It excludes worker CPU, omits a request prefix and can include inspector overhead. Weighted sample time below includes idle frames and is not process-tree CPU seconds.</p><details><summary>Top sampled controller frames</summary>{frames}</details><details><summary>Diagnostic resources and phase timings</summary>{diagnostic_resources(data['controller'])}</details></section>
<section><h2>Rust resource accountant</h2>{accounting}<p>Opt-in getrusage(RUSAGE_THREAD) + monotonic clocks measure admitted sampling, discovery, fresh identity checks and RSS reads. Stages are nested; clock/framework overhead is not separately partitioned. No Node/Python profiler runs in accounting-only mode.</p><p>{accounting_note}</p></section>
<section><h2>Isolation experiments</h2><p>Native structural oracle <a href="https://github.com/RuiCostafrg/oximut/actions/runs/{data['source']['structural_run']}">{data['source']['structural_run']}</a> preserves the raw warm-host leak counterexample. The narrow guard passes its tested fixture scope by recycling dirty hosts. Async/native resources and nested host state remain unproven. No product warm-reuse mode is enabled. Structural wall/CPU/RSS/process counts, phase timings, ratios and CIs are unmeasured/N/A.</p></section>
<section><h2>Next decisions, ranked by current evidence</h2><ol><li>{priority}</li><li><b>Profile worker bootstrap and collection.</b> Controller samples do not explain fork CPU. Warm fresh realms are promising but blocked by isolation gaps.</li><li><b>Reduce baseline overhead safely.</b> Preserve the original oracle and inactive-transform/coverage validation; identify initialization work that can be amortized.</li><li><b>Measure per-test selection and bail.</b> These cannot remove worker startup; validate that early failure never masks teardown or infrastructure errors.</li></ol><p>These are hypotheses and measurement priorities, not promised savings. After a supported optimization, rerun the exact native paired wall/CPU/RSS campaign with bootstrap CIs.</p></section>
<details><summary>Raw resource observations and provenance</summary>{raw_table}<pre>{html.escape(json.dumps({'before':data['build']['before_commit'],'after_test_merge':data['build']['after_commit'],'source_hashes':data['hashes']},indent=2))}</pre><p>Full original artifacts have 14-day workflow retention. The retained summary, raw resource rows, build metadata, and native oracle records persist in the repo; hashes cannot reconstruct omitted binaries/projects/logs.</p></details>'''
    css='''html,body{margin:0;padding:0}body{font:14px/1.55 var(--font-sans,system-ui);color:var(--foreground,#202a37)}:root{--report-space:16px}section{background:var(--card,#f3f6fa);border:1px solid var(--border,#d5dce5);border-radius:var(--radius,12px);padding:16px;margin:14px 0}h2{font-size:19px;margin:0 0 10px}h3{font-size:15px}p{margin:10px 0}.muted{color:var(--muted-foreground,#5c6876);font-size:12px}.notice{border-left:3px solid var(--warning,#a96300);padding:10px}a{color:var(--primary,#3466ba)}table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--border,#d5dce5);white-space:nowrap}th{color:var(--muted-foreground,#5c6876)}.scroll{overflow:auto}svg{width:100%;min-width:620px;display:block}.axis{fill:var(--foreground,#202a37);font:12px var(--font-sans,system-ui)}.bar{fill:var(--chart-1,#517ec0)}.interval{stroke:var(--chart-2,#bd7850);stroke-width:3}.point{fill:var(--chart-2,#bd7850)}.parity{stroke:var(--border,#9ba8b5);stroke-width:1}.target{stroke:var(--success,#37856a);stroke-width:2;stroke-dasharray:4 3}summary{cursor:pointer;font-weight:600;padding:7px 0}details{margin:8px 0}pre{overflow:auto;background:var(--code-background,#eef1f5);padding:12px;border-radius:var(--radius,8px);font-size:11px}li{margin:8px 0}'''
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Oximut performance evidence</title><style>'+css+'</style></head><body>'+content+'</body></html>\n'


def main():
    """Choose compatible retained evidence and write one offline report."""
    parser = argparse.ArgumentParser(description=__doc__)
    recent_control=ROOT/'benchmarks/results/2026-11-05-accounting-control'
    parser.add_argument('--comparison', type=Path, default=recent_control if recent_control.exists() else ROOT/'benchmarks/results/2026-11-05-native-control')
    parser.add_argument('--controller', type=Path, default=ROOT/'benchmarks/results/2026-11-04-controller-job-identities')
    known_accounting=ROOT/'benchmarks/results/2026-11-05-accounting-profile'
    parser.add_argument('--accounting', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.accounting is None and args.comparison.resolve()==recent_control.resolve()
            and known_accounting.exists()):
        args.accounting=known_accounting
    data = collect(args.comparison,args.controller,args.accounting)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(render(data))
    print(args.output)


if __name__ == '__main__':
    main()
