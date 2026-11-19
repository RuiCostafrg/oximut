"""Exercise collection checkpoints, overlap and payload omission."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PRELOAD = Path(__file__).with_name('worker-fetch-timing.cjs').resolve()


@unittest.skipUnless(shutil.which('node'), 'Node unavailable')
class WorkerFetchTimingTests(unittest.TestCase):
    def test_records_spans_and_evaluator_metrics_without_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / 'vitest/dist/workers/forks.js'
            worker.parent.mkdir(parents=True)
            worker.write_text(r'''
const vm = require('node:vm');
process.send = () => true;
require(process.env.TIMING_PRELOAD);
const send = (m, i) => process.send({t:'q',m,i,a:['SECRET']});
process.emit('message', {__vitest_worker_request__:true, type:'run'});
send('fetch','one'); send('fetchWarmModules','two');
process.emit('message', {t:'s',i:'one',r:{cached:true,tmp:'SECRET'}});
process.emit('message', {t:'s',i:'two',r:{SECRET:{}}});
vm.runInThisContext("'use strict';async (__vite_ssr_exports__, other)=>{{}}");
globalThis.__vitest_worker__ = {moduleExecutionInfo: new Map([
    ['SECRET', {duration: 1, selfTime: 0.5}],
    ['SECRET2', {external: true, duration: 0.7, selfTime: 0.7}],
])};
send('onCollected','three');
send('fetch','four');
process.send({__vitest_worker_response__:true, type:'testfileFinished'});
''')
            env = {**os.environ, 'OXIMUT_PROFILE_COLLECTION_DIR': str(root),
                   'OXIMUT_BUDGETED': '1', 'TIMING_PRELOAD': str(PRELOAD)}
            subprocess.run(['node', str(worker)], env=env, check=True, timeout=5)
            file, = root.glob('collection-*.json')
            data = json.loads(file.read_text())
            self.assertEqual(data['outstanding'], 0)
            first, second = data['spans']
            self.assertEqual(first['responseKind'], 'tmpFile')
            self.assertEqual(second['method'], 'fetchWarmModules')
            self.assertEqual(second['snapshotEntries'], 1)
            self.assertLess(second['startMs'], first['endMs'])
            self.assertLess(second['endMs'], data['runToFirstCollectionMs'])
            self.assertEqual(len(data['compiles']), 1)
            self.assertEqual([m['kind'] for m in data['evaluation']], ['inline', 'external'])
            self.assertNotIn('SECRET', file.read_text())
