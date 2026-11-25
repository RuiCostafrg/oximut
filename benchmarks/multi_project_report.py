#!/usr/bin/env python3
"""Aggregate multi-project benchmark cells (benchmarks/multi_project.py) into comparison tables.

Ratios are Oximut divided by Stryker, paired per round, so below 1 means Oximut is cheaper.
The interval is the deterministic paired percentile bootstrap the other campaigns use
(benchmarks/targets.py); with only a few rounds it is close to the min-max range, and the
table says how many rounds back it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import targets  # noqa: E402

METRICS = {'wall': 'wall_seconds', 'cpu': 'cpu_seconds', 'rss': 'peak_aggregate_rss_bytes'}
KIND_ORDER = ('startup', 'large')
KIND_TITLES = {
    'startup': 'Start-up dominated: small scopes (parity corpus)',
    'large': 'Per-mutant dominated: large scopes',
}
WALL_TARGET = targets.THRESHOLDS['wall_seconds']
# A detection-rate gap this large between the tools means the two did not see the same
# test-suite strength on the same code: the speed comparison is suspect until explained.
SCORE_GAP = 0.10
# Stryker generating this many times more (or fewer) mutants makes wall ratios non-comparable per mutant.
COUNT_SKEW = 2.0


def median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def measured_runs(cell, tool):
    return sorted((run for run in cell['runs'] if run['tool'] == tool and not run['warmup'] and run['valid']),
                  key=lambda run: run['round'])


def paired_ratios(cell):
    """{metric: paired_interval} over measured rounds where both tools produced a valid run."""
    oximut = {run['round']: run for run in measured_runs(cell, 'oximut')}
    stryker = {run['round']: run for run in measured_runs(cell, 'stryker')}
    rounds = sorted(oximut.keys() & stryker.keys())
    result = {}
    for name, key in METRICS.items():
        pairs = [(oximut[i][key], stryker[i][key]) for i in rounds
                 if oximut[i].get(key) is not None and stryker[i].get(key)]
        if pairs:
            result[name] = {**targets.paired_interval(pairs), 'rounds': len(pairs)}
    return result


def rate_text(counts):
    detected = (counts.get('killed', 0) + counts.get('timeout', 0))
    total = detected + counts.get('survived', 0) + counts.get('no-coverage', 0)
    return detected / total if total else None


def count_text(counts):
    """killed/survived/no-coverage, with a timeout count appended when there are any."""
    text = f"{counts.get('killed', 0)}/{counts.get('survived', 0)}/{counts.get('no-coverage', 0)}"
    extra = [f"{counts[name]} {label}" for name, label in (('timeout', 'timeout'), ('invalid', 'invalid'))
             if counts.get(name)]
    return text + (f" (+{', '.join(extra)})" if extra else '')


def summarize_cell(cell):
    """One row of evidence for a cell, with the red flags it earns."""
    project = cell['project']
    summary = {'project': project['name'], 'kind': project['kind'], 'commit': project['commit'],
               'repository': project['repository'], 'workers': cell['workers'], 'scope': cell.get('scope'),
               'cpu_model': cell['machine']['cpu_model'], 'usable_cpus': cell['machine']['usable_cpus'],
               'status': cell['status'], 'error': cell.get('error'), 'flags': []}
    if cell['status'] != 'ok':
        return summary
    oximut, stryker = measured_runs(cell, 'oximut'), measured_runs(cell, 'stryker')
    vitest = measured_runs(cell, 'vitest')
    if not oximut or not stryker:
        summary.update(status='failed', error='no valid measured round for both tools')
        return summary
    ratios = paired_ratios(cell)
    summary['ratios'] = {name: {'median': value['median'], 'low': value['bootstrap_95_percent'][0],
                                'high': value['bootstrap_95_percent'][1], 'rounds': value['rounds'],
                                'paired': value['paired_ratios']}
                         for name, value in ratios.items()}
    for tool, runs in (('oximut', oximut), ('stryker', stryker)):
        summary[tool] = {
            'mutants': runs[0]['mutants'], 'counts': runs[0]['counts'],
            'wall_seconds': median(run['wall_seconds'] for run in runs),
            'cpu_seconds': median(run['cpu_seconds'] for run in runs),
            'rss_bytes': median(run['peak_aggregate_rss_bytes'] for run in runs),
            'outcome_sha256': runs[0]['outcome_sha256'],
            'stable_outcomes': len({run['outcome_sha256'] for run in runs}) == 1,
            'rounds': len(runs),
        }
    summary['oximut']['complete'] = all(run.get('complete') for run in oximut)
    summary['oximut']['phase_wall_ms'] = oximut[0].get('phase_wall_ms')
    summary['oximut']['diagnostics'] = oximut[0].get('diagnostics')
    summary['stryker']['log'] = stryker[0].get('log')
    summary['vitest'] = {'wall_seconds': median(run['wall_seconds'] for run in vitest),
                         'tests': vitest[0].get('tests') if vitest else None}
    summary['scope_comparison'] = cell.get('scope_comparison')
    summary['flags'] = flags_for(summary)
    return summary


def flags_for(summary):
    flags = []
    oximut, stryker = summary['oximut'], summary['stryker']
    wall = summary['ratios'].get('wall')
    if wall:
        if wall['median'] > 1:
            flags.append('Oximut slower than Stryker')
        elif wall['median'] > WALL_TARGET:
            flags.append(f'wall ratio above the {WALL_TARGET:g}x target')
    gap_rates = (rate_text(oximut['counts']), rate_text(stryker['counts']))
    if None not in gap_rates and abs(gap_rates[0] - gap_rates[1]) >= SCORE_GAP:
        flags.append(f'score gap {100 * (gap_rates[0] - gap_rates[1]):+.1f} pp (Oximut - Stryker)')
    comparison = summary.get('scope_comparison')
    if comparison and comparison['shared_disagree']:
        flags.append(f"{comparison['shared_disagree']}/{comparison['shared']} shared mutants classified differently")
    if oximut['mutants'] and stryker['mutants']:
        skew = stryker['mutants'] / oximut['mutants']
        if skew >= COUNT_SKEW or skew <= 1 / COUNT_SKEW:
            flags.append(f'Stryker ran {skew:.1f}x as many mutants')
    for name, tool in (('Oximut', oximut), ('Stryker', stryker)):
        if not tool['stable_outcomes']:
            flags.append(f'{name} outcomes differ between rounds')
    if not oximut['complete']:
        flags.append('Oximut run incomplete (timeout or invalid mutants)')
    if summary['oximut']['rounds'] != summary['stryker']['rounds']:
        flags.append('unequal valid rounds')
    return flags


def fmt_ratio(value):
    return f"{value['median']:.3f}x [{value['low']:.3f}, {value['high']:.3f}]"


def fmt_plain(value):
    return f"{value['median']:.3f}x"


def seconds(value):
    return f'{value:.1f}' if value is not None else 'n/a'


def table_row(summary):
    head = [summary['project'], str(summary['workers']), summary['cpu_model']]
    if summary['status'] == 'skipped':
        return head + ['skipped: oversubscribed'] + [''] * 8 + [summary['error']]
    if summary['status'] != 'ok':
        return head + ['**failed**'] + [''] * 8 + [str(summary['error'])]
    oximut, stryker, ratios = summary['oximut'], summary['stryker'], summary['ratios']
    return head + [
        f"{oximut['mutants']} / {stryker['mutants']}",
        seconds(oximut['wall_seconds']), seconds(stryker['wall_seconds']),
        f"{fmt_ratio(ratios['wall'])} (n={ratios['wall']['rounds']})" if 'wall' in ratios else 'n/a',
        fmt_plain(ratios['cpu']) if 'cpu' in ratios else 'n/a',
        fmt_plain(ratios['rss']) if 'rss' in ratios else 'n/a',
        f"`{oximut['outcome_sha256'][:12]}`",
        count_text(oximut['counts']), count_text(stryker['counts']),
        '; '.join(summary['flags']) or '-',
    ]


HEADER = ['Project', 'Workers', 'CPU', 'Mutants Oximut / Stryker', 'Oximut wall s', 'Stryker wall s',
          'Wall ratio [95% CI]', 'CPU ratio', 'RSS ratio', 'Oximut outcome sha',
          'Oximut K/S/NC', 'Stryker K/S/NC', 'Flags']


def markdown_table(rows):
    return markdown_table_with(HEADER, rows)


def diagnostics_rows(summaries):
    """Where the time goes: suite cost, Oximut's mutation phase share, per-mutant wall time of each tool."""
    rows = []
    for summary in summaries:
        if summary['status'] != 'ok':
            continue
        oximut, stryker = summary['oximut'], summary['stryker']
        phases = oximut.get('phase_wall_ms') or {}
        execution = phases.get('mutationExecution')
        wall_ms = 1000 * oximut['wall_seconds']
        stryker_log = stryker.get('log') or {}
        rows.append([
            summary['project'], str(summary['workers']),
            seconds(summary['vitest']['wall_seconds']),
            f'{100 * execution / wall_ms:.0f}%' if execution is not None and wall_ms else 'n/a',
            f"{wall_ms / oximut['mutants']:.0f}", f"{1000 * stryker['wall_seconds'] / stryker['mutants']:.0f}",
            seconds(stryker_log.get('initial_run_seconds')),
            f"{100 * stryker_log['mutation_phase_seconds'] / summary['stryker']['wall_seconds']:.0f}%"
            if stryker_log.get('mutation_phase_seconds') is not None else 'n/a',
        ])
    return rows


DIAGNOSTIC_HEADER = ['Project', 'Workers', 'Vitest suite s', 'Oximut mutation-phase share of wall',
                     'Oximut ms/mutant (wall)', 'Stryker ms/mutant (wall)', 'Stryker initial run s',
                     'Stryker mutation-phase share of wall']


def scope_rows(summaries):
    rows = []
    for summary in summaries:
        comparison = summary.get('scope_comparison')
        if summary['status'] != 'ok' or not comparison:
            continue
        rows.append([summary['project'], str(summary['workers']), str(comparison['oximut_mutants']),
                     str(comparison['stryker_mutants']), str(comparison['shared']),
                     str(comparison['oximut_only']), str(comparison['stryker_only']),
                     f"{comparison['shared_agree']}/{comparison['shared']}",
                     f"{100 * rate_text(summary['oximut']['counts']):.1f}%"
                     if rate_text(summary['oximut']['counts']) is not None else 'n/a',
                     f"{100 * rate_text(summary['stryker']['counts']):.1f}%"
                     if rate_text(summary['stryker']['counts']) is not None else 'n/a'])
    return rows


SCOPE_HEADER = ['Project', 'Workers', 'Oximut mutants', 'Stryker mutants', 'Same edit in both', 'Oximut only',
                'Stryker only', 'Shared: same verdict', 'Oximut detected', 'Stryker detected']


def sort_key(summary):
    kind = KIND_ORDER.index(summary['kind']) if summary['kind'] in KIND_ORDER else len(KIND_ORDER)
    return kind, summary['project'], summary['workers']


def render_markdown(summaries):
    summaries = sorted(summaries, key=sort_key)
    out = ['## Multi-project benchmark: Oximut (default flags) vs StrykerJS', '',
           'Ratios are Oximut / Stryker, paired per round: below 1 means Oximut is faster or cheaper. '
           f'`n` is the number of paired rounds behind the interval. K/S/NC is killed / survived / no-coverage '
           '(timeouts are appended when present). Each cell ran on its own GitHub runner, so compare ratios '
           'within a row, not walls across rows.', '']
    scopes = sorted({summary['scope'] for summary in summaries if summary.get('scope')})
    if scopes:
        out += [f"Scope: {', '.join(scopes)}. `matched` limits Stryker to the operators that correspond to Oximut's "
                'three families (arithmetic, boolean-literal, comparison-boundary); the tools still generate '
                'different mutants, so the mutant counts are shown.', '']
    for kind in KIND_ORDER:
        group = [summary for summary in summaries if summary['kind'] == kind]
        if group:
            out += [f'### {KIND_TITLES[kind]}', '', markdown_table([table_row(s) for s in group]), '']
    diagnostics = diagnostics_rows(summaries)
    if diagnostics:
        out += ['### Where the time goes', '', markdown_table_with(DIAGNOSTIC_HEADER, diagnostics), '']
    scopes_table = scope_rows(summaries)
    if scopes_table:
        out += ['### Mutant overlap and agreement', '', markdown_table_with(SCOPE_HEADER, scopes_table), '']
    weak = [s for s in summaries if s['status'] == 'ok' and s['flags']]
    if weak:
        out += ['### Flags', ''] + [f"- **{s['project']} x{s['workers']}**: {'; '.join(s['flags'])}" for s in weak] + ['']
    return '\n'.join(out)


def markdown_table_with(header, rows):
    lines = ['| ' + ' | '.join(header) + ' |', '| ' + ' | '.join(['---'] * len(header)) + ' |']
    lines += ['| ' + ' | '.join(cell.replace('|', '\\|') for cell in row) + ' |' for row in rows]
    return '\n'.join(lines)


def load_cells(directory):
    cells = []
    for path in sorted(Path(directory).rglob('cell.json')):
        cells.append(json.loads(path.read_text()))
    return cells


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('cells', type=Path, help='directory searched recursively for cell.json files')
    parser.add_argument('--output', type=Path, help='directory for summary.json and summary.md')
    args = parser.parse_args(argv)
    cells = load_cells(args.cells)
    if not cells:
        print(f'no cell.json under {args.cells}', file=sys.stderr)
        return 1
    summaries = [summarize_cell(cell) for cell in cells]
    markdown = render_markdown(summaries)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'summary.json').write_text(json.dumps(summaries, indent=2, sort_keys=True) + '\n')
        (args.output / 'summary.md').write_text(markdown + '\n')
    print(markdown)
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a') as handle:
            handle.write(markdown + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
