import { createRequire } from 'node:module';
import { TestRunner } from 'vitest';

// Opt-in diagnostic timeline (OXIMUT_TRACE_TIMELINE); see timeline.cjs. Unset, the
// runner below is unchanged.
const trace = process.env.OXIMUT_TRACE_TIMELINE
  ? createRequire(import.meta.url)('./timeline.cjs')
  : undefined;

// bail must remain zero: stock bail cancels every project in the controller.
export default class LaneRunner extends TestRunner {
  constructor(config) {
    super(config);
    if (!trace) return;
    // Vitest-side phases of one lane worker: runner built, collection (importing
    // and registering the test files), execution of the collected tests.
    const mark = (event) =>
      trace.emit('worker', event, {
        mutant: process.env.OXIMUT_ACTIVE_MUTANT,
        // The precollect template's own collect has no mutant and is warm; a
        // mutant's worker forked from a template is templated (its second start).
        warm: process.env.OXIMUT_ZYGOTE_WARM === '1',
        templated: !!process.env.OXIMUT_ZYGOTE_TEMPLATE && process.env.OXIMUT_ZYGOTE_WARM !== '1',
      });
    mark('runner_created');
    this.onBeforeCollect = () => mark('collect_begin');
    this.onCollected = () => mark('collect_end');
    this.onBeforeRunFiles = () => mark('execute_begin');
    const after = this.onAfterRunFiles.bind(this);
    this.onAfterRunFiles = (...rest) => {
      try {
        return after(...rest);
      } finally {
        mark('execute_end');
      }
    };
  }

  onAfterRunTask(task) {
    super.onAfterRunTask(task);
    if (task.result?.state === 'fail') this.cancel('test-failure');
  }
}
