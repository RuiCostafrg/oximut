#!/usr/bin/env python3
"""Require all four W13 configurations before publishing any scored comparisons."""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import m6

CONFIGS = [(scope, workers) for scope in ('colord', 'colord-scale') for workers in (2, 4)]


def summarize(root):
    configs, errors = {}, []
    for scope, workers in CONFIGS:
        name = f'{scope}-{workers}w'
        try:
            config = json.loads((root / name / 'score.json').read_text())
            if config['status'] != 'completed' or not all(config['evaluations'][mode]['correctness_gate']['passed'] for mode in ('prepared', 'first_use')):
                errors.append(f'{name}: incomplete/failed correctness or build gate')
            configs[name] = config
        except (OSError, ValueError, KeyError) as error:
            errors.append(f'{name}: {error}')
    if len({c.get('head') for c in configs.values()}) > 1:
        errors.append('Candidate source changed between configurations')
    valid = not errors
    result = {'correctness_gate': {'passed': valid, 'errors': errors}, 'configs': copy.deepcopy(configs),
              'targets_met': {mode: valid and all(c['evaluations'][mode]['targets_met'] for c in configs.values()) for mode in ('prepared', 'first_use')},
              'policy': 'All four declared scopes/concurrencies required. Mission misses are evidence, not retry triggers. Five pairs are exploratory host/corpus evidence; no general noninferiority conclusion.'}
    if not valid:
        for config in result['configs'].values():
            for evaluation in config.get('evaluations', {}).values():
                evaluation['comparisons'] = {}
                evaluation['targets_met'] = False
                evaluation['correctness_gate']['passed'] = False
                evaluation['correctness_gate']['errors'].append('Global four-configuration gate failed; ratios suppressed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result = summarize(args.root)
    m6.write_json(args.root / 'campaign-summary.json', result)
    raise SystemExit(0 if result['correctness_gate']['passed'] else 1)
