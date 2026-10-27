"""Signing must never accept mismatched or incomplete R3 evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('check_evidence', Path(__file__).parents[1] / 'scripts/check-source-evidence.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class EvidenceTests(unittest.TestCase):
    def test_only_complete_evidence_for_the_same_bytes_and_revision_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, evidence = root / 'source.tar.gz', root / 'evidence.json'
            archive.write_bytes(b'exact source archive')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            data = dict(gate='R3', status='passed', archiveSha256=digest,
                        source={'commit': 'a' * 40}, version='1.0.0-rc.1', buildCount=1,
                        extractedSourceRemoved=True, binarySha256='b' * 64,
                        tests=[{'phase': 'installed', 'passed': True}, {'phase': 'relocated', 'passed': True}])
            evidence.write_text(json.dumps(data))
            self.assertEqual(CHECK.check(archive, evidence, 'a' * 40), digest)
            for key, value in [('status', 'failed'), ('archiveSha256', '0' * 64),
                               ('source', {'commit': 'c' * 40}), ('version', '1.0.0'),
                               ('buildCount', 2), ('extractedSourceRemoved', False),
                               ('binarySha256', ''), ('tests', []),
                               ('tests', [{'phase': 'installed', 'passed': True}] * 2),
                               ('tests', [{'phase': 'installed', 'passed': True}, {'phase': 'relocated', 'passed': False}])]:
                changed = copy.deepcopy(data)
                changed[key] = value
                evidence.write_text(json.dumps(changed))
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    CHECK.check(archive, evidence, 'a' * 40)
