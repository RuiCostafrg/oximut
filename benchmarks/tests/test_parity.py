"""Outcome parity harness: it must detect and report any per-mutant difference between modes."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'parity'))
import run_parity as parity

SOURCE = 'export const a = 1 + 2;\nexport const b = 3 < 4;\n'


def mutant(project, mutant_id, replacement, outcome, line=1, operator='arithmetic', filename='src/a.ts'):
    """A report entry replacing the character at `line` col 1 with `replacement` (offsets are irrelevant)."""
    return {'id': mutant_id, 'file': str(project / filename), 'operator': operator, 'replacement': replacement,
            'outcome': outcome,
            'location': {'start': {'offset': 0, 'line': line, 'column': 1},
                         'end': {'offset': 6, 'line': line, 'column': 7}}}


class ProjectCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = Path(directory.name)
        (self.project / 'src').mkdir()
        (self.project / 'src/a.ts').write_text(SOURCE)

    def records(self, *mutants):
        return parity.mutant_records({'mutants': list(mutants)}, self.project)


class RecordTests(ProjectCase):
    def test_digest_ignores_mutant_ids_and_report_order(self):
        first = self.records(mutant(self.project, 'x1', 'const', 'killed'),
                             mutant(self.project, 'x2', 'let ', 'survived', line=2))
        second = self.records(mutant(self.project, 'y2', 'let ', 'survived', line=2),
                              mutant(self.project, 'y1', 'const', 'killed'))
        self.assertEqual(parity.outcome_digest(first), parity.outcome_digest(second))

    def test_digest_changes_with_one_outcome(self):
        killed = self.records(mutant(self.project, 'x1', 'const', 'killed'))
        survived = self.records(mutant(self.project, 'x1', 'const', 'survived'))
        self.assertNotEqual(parity.outcome_digest(killed), parity.outcome_digest(survived))

    def test_digest_matches_the_benchmark_harness_definition(self):
        records = self.records(mutant(self.project, 'x1', 'const', 'killed'))
        identity = parity.m6.mutation_identity('src/a.ts', SOURCE, records_location(), 'const')
        self.assertEqual(parity.outcome_digest(records), parity.m6.digest({identity: 'killed'}))

    def test_two_mutants_with_the_same_edit_are_an_ambiguous_scope(self):
        with self.assertRaisesRegex(parity.ParityError, 'ambiguous'):
            self.records(mutant(self.project, 'x1', 'const', 'killed'),
                         mutant(self.project, 'x2', 'const', 'survived'))


def records_location():
    return {'start': {'offset': 0, 'line': 1, 'column': 1}, 'end': {'offset': 6, 'line': 1, 'column': 7}}


class CompareTests(ProjectCase):
    def test_identical_outcomes_have_no_differences(self):
        reference = self.records(mutant(self.project, 'x1', 'const', 'killed'))
        other = self.records(mutant(self.project, 'other-id', 'const', 'killed'))
        self.assertEqual(parity.compare_records(reference, other), [])

    def test_changed_outcome_is_reported_with_where_what_and_both_outcomes(self):
        reference = self.records(mutant(self.project, 'x1', 'const', 'killed'),
                                 mutant(self.project, 'x2', 'let ', 'survived', line=2, operator='comparison'))
        other = self.records(mutant(self.project, 'x1', 'const', 'killed'),
                             mutant(self.project, 'x2', 'let ', 'killed', line=2, operator='comparison'))
        differences = parity.compare_records(reference, other)
        self.assertEqual(len(differences), 1)
        self.assertEqual({k: differences[0][k] for k in ('id', 'file', 'line', 'operator', 'reference', 'mode')},
                         {'id': 'x2', 'file': 'src/a.ts', 'line': 2, 'operator': 'comparison',
                          'reference': 'survived', 'mode': 'killed'})

    def test_mutant_missing_from_either_side_is_a_difference(self):
        reference = self.records(mutant(self.project, 'x1', 'const', 'killed'))
        other = self.records(mutant(self.project, 'x1', 'const', 'killed'),
                             mutant(self.project, 'x2', 'let ', 'survived', line=2))
        extra = parity.compare_records(reference, other)
        self.assertEqual([(d['reference'], d['mode']) for d in extra], [('<absent>', 'survived')])
        missing = parity.compare_records(other, reference)
        self.assertEqual([(d['reference'], d['mode']) for d in missing], [('survived', '<absent>')])

    def test_no_coverage_is_not_treated_as_survived(self):
        reference = self.records(mutant(self.project, 'x1', 'const', 'survived'))
        other = self.records(mutant(self.project, 'x1', 'const', 'no-coverage'))
        self.assertEqual(len(parity.compare_records(reference, other)), 1)

    def test_formatted_report_names_every_differing_mutant(self):
        reference = self.records(mutant(self.project, 'x1', 'const', 'killed'))
        other = self.records(mutant(self.project, 'x1', 'const', 'survived'))
        text = parity.format_differences('proj', 'zygote', parity.compare_records(reference, other))
        self.assertIn('MISMATCH proj / zygote: 1 mutant(s)', text)
        self.assertIn('x1 src/a.ts:1 arithmetic: reference=killed zygote=survived', text)


class RefinementTests(ProjectCase):
    """Test selection may turn survived into no-coverage, but only for mutants the corpus approved."""

    SELECTION = {'mode': 'none', 'files': [], 'selectedTests': 0, 'totalTests': 9,
                 'reasons': ['complete-baseline-without-hits']}

    def pair(self, selection=None, outcome='no-coverage'):
        reference = self.records(dict(mutant(self.project, 'x1', 'const', 'survived'), original='1 + 2'))
        entry = dict(mutant(self.project, 'x1', 'const', outcome), original='1 + 2')
        if selection is not None:
            entry['selection'] = selection
        return reference, self.records(entry)

    def allowed(self, reference):
        return {parity.refinement_key(next(iter(reference.values())))}

    def test_approved_refinement_is_not_a_difference_and_adjusts_the_digest(self):
        reference, other = self.pair(self.SELECTION)
        allowed = self.allowed(reference)
        self.assertEqual(parity.compare_records(reference, other, allowed), [])
        explained = parity.explained_refinements(reference, other, allowed)
        self.assertEqual(len(explained), 1)
        self.assertEqual(parity.outcome_digest_adjusted(other, set(explained)), parity.outcome_digest(reference))
        self.assertNotEqual(parity.outcome_digest(other), parity.outcome_digest(reference))

    def test_unapproved_refinement_is_a_difference(self):
        reference, other = self.pair(self.SELECTION)
        self.assertEqual(len(parity.compare_records(reference, other)), 1)

    def test_approval_needs_selection_evidence_of_a_complete_baseline_without_hits(self):
        reference, other = self.pair(dict(self.SELECTION, reasons=['incomplete-baseline']))
        self.assertEqual(len(parity.compare_records(reference, other, self.allowed(reference))), 1)
        reference, other = self.pair(None)
        self.assertEqual(len(parity.compare_records(reference, other, self.allowed(reference))), 1)

    def test_approval_never_covers_other_outcome_changes(self):
        reference, other = self.pair(self.SELECTION, outcome='killed')
        self.assertEqual(len(parity.compare_records(reference, other, self.allowed(reference))), 1)


def report(outcomes, diagnostics=(), complete=True, root=None):
    return {'summary': {'complete': complete}, 'diagnostics': list(diagnostics),
            'mutants': outcomes if outcomes is not None else []}


class RunGateTests(unittest.TestCase):
    def test_survivors_are_comparable_but_incomplete_or_failed_runs_are_not(self):
        ok = report([{}])
        self.assertEqual(parity.check_run('zygote', ok, 0), [])
        self.assertEqual(parity.check_run('zygote', ok, 1), [])
        self.assertTrue(parity.check_run('zygote', ok, 2))
        self.assertTrue(parity.check_run('zygote', report([{}], complete=False), 0))
        self.assertTrue(parity.check_run('zygote', report([]), 0))

    def test_a_run_is_comparable_with_timeouts_only_when_each_one_is_approved(self):
        key = ('src/a.ts', 2, '+', '-')
        other = ('src/a.ts', 3, '*', '/')

        def timed_out(**extra):
            summary = {'complete': False, 'timeout': 1, 'invalid': 0, 'infrastructureError': 0,
                       'resourceExhausted': 0, 'skipped': 0, **extra}
            return {'summary': summary, 'diagnostics': [], 'mutants': [{}]}
        # The exit code is 2 for any timeout; approved ones are the only way that passes.
        self.assertEqual(parity.check_run('zygote', timed_out(), 2, {key}, {key}), [])
        self.assertTrue(parity.check_run('zygote', timed_out(), 2, set(), {key}))
        self.assertTrue(parity.check_run('zygote', timed_out(timeout=2), 2, {key}, {key, other}))
        # Nothing else that makes a run incomplete is tolerated alongside them.
        for cause in ('invalid', 'infrastructureError', 'resourceExhausted', 'skipped'):
            self.assertTrue(parity.check_run('zygote', timed_out(**{cause: 1}), 2, {key}, {key}), cause)
        # Exit 2 stays an error when the run has no approved timeout to explain it.
        self.assertTrue(parity.check_run('zygote', report([{}], complete=False), 2, {key}, frozenset()))

    def test_zygote_mode_requires_forks_and_no_failures(self):
        diagnostics = ['execution=persistent; zygote=on']
        counters = dict(zygotes=1, forks=5, exits=5, zygoteFailures=0, fallbacks=0)
        self.assertEqual(parity.check_engagement('zygote', report([{}], diagnostics), counters), [])
        self.assertTrue(parity.check_engagement('zygote', report([{}], diagnostics), dict(counters, forks=0)))
        self.assertTrue(parity.check_engagement('zygote', report([{}], diagnostics), dict(counters, zygoteFailures=1)))
        self.assertTrue(parity.check_engagement('zygote', report([{}], ['zygote=off']), counters))

    def test_non_zygote_modes_must_not_use_the_zygote(self):
        idle = dict(zygotes=0, forks=0, exits=0, zygoteFailures=0, fallbacks=0)
        self.assertEqual(parity.check_engagement('persistent', report([{}], ['execution=persistent']), idle), [])
        self.assertTrue(parity.check_engagement('persistent', report([{}], ['zygote=on']), idle))
        self.assertTrue(parity.check_engagement('reference', report([{}]), dict(idle, forks=1)))

    def test_fast_mode_must_report_each_flag_it_asked_for(self):
        counters = dict(zygotes=1, forks=5, exits=5, zygoteFailures=0, fallbacks=0)
        mode = 'defaults'
        full = ['zygote=on; zygote-precollect=on; rolling-lanes=on']
        self.assertEqual(parity.check_engagement(mode, report([{}], full), counters), [])
        partial = ['zygote=on; zygote-precollect=on; rolling-lanes=off']
        self.assertTrue(parity.check_engagement(mode, report([{}], partial), counters))


FAKE_OXIMUT = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in Oximut: kills every mutant, except that zygote modes flip one outcome when FLIP is set."""
    import json, os, sys
    args = sys.argv[1:]
    value = lambda flag: args[args.index(flag) + 1]
    root = value('--root')
    # Mirrors the CLI defaults: persistent execution with the zygote, precollect and
    # rolling lanes on unless a flag turns them off.
    execution = value('--execution') if '--execution' in args else 'persistent'
    zygote = execution == 'persistent' and '--no-zygote' not in args
    precollect = zygote and '--no-zygote-precollect' not in args and '--no-test-selection' not in args
    rolling = execution == 'persistent' and '--no-rolling-lanes' not in args
    flip = os.environ.get('FAKE_FLIP') == '1' and zygote
    diagnostics = ['execution=' + execution, 'zygote=' + ('on' if zygote else 'off')]
    if precollect:
        diagnostics.append('zygote-precollect=on')
    if rolling:
        diagnostics.append('rolling-lanes=on')
    mutants = []
    for index, (replacement, line) in enumerate([('const', 1), ('let ', 2)]):
        outcome = 'killed'
        if flip and index == 1:
            outcome = 'survived'
        mutants.append({'id': 'id%d' % index, 'file': root + '/src/a.ts', 'operator': 'arithmetic',
                        'replacement': replacement, 'outcome': outcome,
                        'location': {'start': {'offset': 0, 'line': line, 'column': 1},
                                     'end': {'offset': 6, 'line': line, 'column': 7}}})
    if zygote:
        counters = os.environ['OXIMUT_ZYGOTE_COUNTERS_DIR']
        json.dump({'zygotes': 1, 'forks': 2, 'exits': 2, 'zygoteFailures': 0, 'fallbacks': 0},
                  open(counters + '/worker.json', 'w'))
    json.dump({'summary': {'complete': True}, 'diagnostics': diagnostics, 'mutants': mutants,
               'resources': {'effectiveWorkers': int(value('--workers'))}}, open(value('--report'), 'w'))
    sys.exit(0)
    ''')


class EndToEndTests(ProjectCase):
    """Drive main() with a stand-in binary: the exit code and output are the user-visible behaviour."""

    def setUp(self):
        super().setUp()
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)
        binary = Path(self.out.name) / 'fake-oximut'
        binary.write_text(FAKE_OXIMUT)
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        self.binary = binary
        corpus = Path(self.out.name) / 'corpus.json'
        corpus.write_text(json.dumps({'schemaVersion': 1, 'workers': 4, 'projects': [{
            'name': 'fake', 'repository': 'unused', 'commit': 'a' * 40, 'vitestConfig': 'unused',
            'configFile': 'vitest.config.ts', 'mutate': ['src/a.ts']}]}))
        self.corpus = corpus

    def run_main(self, flip):
        stdout, stderr = io.StringIO(), io.StringIO()
        results = Path(self.out.name) / 'results'
        env = {'FAKE_FLIP': '1' if flip else '0'}
        with mock.patch.dict(os.environ, env), mock.patch.object(parity, 'fetch_project', return_value=self.project), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = parity.main(['--binary', str(self.binary), '--corpus', str(self.corpus),
                                '--output', str(results)])
        return code, stdout.getvalue(), stderr.getvalue(), json.loads((results / 'summary.json').read_text())

    def test_identical_modes_pass_and_record_one_sha_per_mode(self):
        code, stdout, stderr, summary = self.run_main(flip=False)
        self.assertEqual(code, 0, stderr)
        shas = {m: d['mutation_identity_outcome_sha256'] for m, d in summary[0]['modes'].items()}
        self.assertEqual(set(shas), set(parity.MODE_NAMES))
        self.assertEqual(len(set(shas.values())), 1)
        self.assertEqual({d['effective_workers'] for d in summary[0]['modes'].values()}, {4})
        self.assertIn('outcome parity ok', stdout)

    def test_one_flipped_outcome_in_a_fast_mode_fails_with_the_per_mutant_diff(self):
        code, stdout, stderr, summary = self.run_main(flip=True)
        self.assertEqual(code, 1)
        self.assertIn('MISMATCH fake / zygote: 1 mutant(s)', stderr)
        self.assertIn('id1 src/a.ts:2 arithmetic: reference=killed zygote=survived', stderr)
        self.assertIn('MISMATCH fake / defaults', stderr)
        self.assertNotIn('MISMATCH fake / persistent', stderr)
        self.assertEqual(set(summary[0]['mismatches']), {'zygote', 'defaults'})
        self.assertIn('**NO**', stdout)


class CorpusTests(unittest.TestCase):
    def test_every_project_is_pinned_and_has_its_config_and_sources(self):
        corpus = parity.load_corpus()
        self.assertGreaterEqual(len(corpus['projects']), 4)
        for project in corpus['projects']:
            self.assertTrue((parity.HERE / project['vitestConfig']).is_file(), project['name'])
            self.assertTrue(project['mutate'], project['name'])
            self.assertTrue(project['repository'].startswith('https://github.com/'), project['name'])

    def test_a_branch_name_instead_of_a_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'corpus.json'
            path.write_text(json.dumps({'workers': 4, 'projects': [{'name': 'x', 'commit': 'main'}]}))
            with self.assertRaises(parity.ParityError):
                parity.load_corpus(path)


if __name__ == '__main__':
    unittest.main()
