#!/usr/bin/env python3
"""Replay potential per-test plans from completed multi-project benchmark reports.

Counts describe requested tests, before early bail, and do not estimate wall time.
"""
import argparse
import collections
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def project_selection(report):
    coverage = report['baseline'].get('coverage')
    identities = {test['id']: test for test in coverage['tests']} if coverage else {}
    names = collections.Counter((t['file'], t['name']) for t in identities.values())
    hits = {hit['mutantId']: hit for hit in coverage['hits']} if coverage else {}
    if coverage and (len(identities) != len(coverage['tests']) or len(hits) != len(coverage['hits'])):
        raise ValueError('Duplicate baseline test or mutant identities')

    outcomes = collections.defaultdict(collections.Counter)
    for mutant in report['mutants']:
        destination = outcomes[mutant['outcome']]
        selection = mutant.get('selection')
        destination['mutants'] += 1
        if mutant['outcome'] == 'timeout' and any(
            message.startswith('Job deadline exceeded') for message in mutant['errors']
        ):
            destination['hard_deadlines'] += 1
            destination['hard_deadline_budget_ms'] += mutant['deadlineMs']
        if selection is None:
            destination['unobserved_selection'] += 1
            continue

        selected_tests = selection['selectedTests']
        destination['planned_file_tests'] += selected_tests
        wanted = selected_tests
        if selection['mode'] == 'files' and coverage and coverage['complete'] and not coverage['reasons']:
            hit = hits[mutant['id']]
            ids = set(hit['tests'])
            for start, end in hit.get('testRanges', []):
                if not 0 <= start <= end < len(coverage['tests']):
                    raise ValueError('Invalid test range')
                ids.update(t['id'] for t in coverage['tests'][start:end + 1])
            files = set(selection['files'])
            if (not ids or hit['outsideTest'] or not ids <= identities.keys()
                    or {identities[i]['file'] for i in ids} != files):
                raise ValueError(f'File selection does not reconcile for {mutant["id"]}')
            tests_by_file = collections.Counter(identities[i]['file'] for i in ids)
            ambiguous_files = {identities[i]['file'] for i in ids
                               if names[identities[i]['file'], identities[i]['name']] > 1}
            wanted = len(ids)
            if ambiguous_files:
                # An ambiguous name preserves its entire file.
                all_by_file = collections.Counter(t['file'] for t in identities.values())
                wanted += sum(all_by_file[f] - tests_by_file[f] for f in ambiguous_files)
            if wanted > selected_tests:
                raise ValueError(f'Projected selection exceeds observed selection for {mutant["id"]}')
        destination['projected_tests'] += wanted
        destination['mutants_with_fewer_tests'] += wanted < selected_tests
    return {outcome: dict(values) for outcome, values in sorted(outcomes.items())}


def summarize(root):
    cells = []
    for path in sorted(root.glob('multi-project-cell-*/cell.json')):
        cell = load(path)
        if cell['status'] != 'ok':
            continue
        rounds = []
        for index in range(cell['warmups'], cell['warmups'] + cell['rounds']):
            report = load(path.parent / f'{index:02d}-oximut/report.json')
            rounds.append({'round': index, 'outcomes': project_selection(report)})
        cells.append({'project': cell['project'], 'workers': cell['workers'], 'rounds': rounds})
    if not cells:
        raise ValueError('No completed benchmark cells found')
    return {
        'scope': 'Projected test plans only. Whole-file and ambiguous-name fallbacks preserved. '
                 'Deadline budgets are configured budgets, not measured slot times. '
                 'Outcome parity and speed require execution.',
        'cells': cells,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.artifacts), indent=2))


if __name__ == '__main__':
    main()
