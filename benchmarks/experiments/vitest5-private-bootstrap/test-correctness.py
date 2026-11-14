"""Exercise the actual gate wiring against recovered native stock evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('w13_correctness',HERE/'correctness.py')
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)


class GateWiring(unittest.TestCase):
    def setUp(self):
        self.fixture=json.loads((HERE/'stock-gate-fixture.json').read_text())
        self.manifest=subject.workload.load_manifest('colord')

    def test_actual_native_stock_ledger_passes_unchanged_gates(self):
        subject.validate_oximut_gates(self.fixture,self.manifest,2)

    def test_missing_instrumented_validation_is_not_accepted(self):
        self.fixture['resources']['disabledValidationRuns']=[]
        with self.assertRaisesRegex(AssertionError,'validation ledger'):
            subject.validate_oximut_gates(self.fixture,self.manifest,2)

    def test_admission_pause_and_reduced_workers_are_not_accepted(self):
        for key,value in [('admissionPauses',1),('effectiveWorkers',1)]:
            row=copy.deepcopy(self.fixture);row['resources'][key]=value
            with self.assertRaisesRegex(AssertionError,'concurrency/admission'):
                subject.validate_oximut_gates(row,self.manifest,2)


if __name__=='__main__':unittest.main()
