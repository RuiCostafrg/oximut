import { VitestTestRunner } from 'vitest/runners';

// bail=0 is essential: Vitest wraps this hook with global bail handling.
export default class LocalBailRunner extends VitestTestRunner {
  onAfterRunTask(task) {
    super.onAfterRunTask(task);
    if (task.result?.state === 'fail') this.cancel('test-failure');
  }
}
