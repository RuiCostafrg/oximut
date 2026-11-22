import { TestRunner } from 'vitest';

// bail must remain zero: stock bail cancels every project in the controller.
export default class LaneRunner extends TestRunner {
  onAfterRunTask(task) {
    super.onAfterRunTask(task);
    if (task.result?.state === 'fail') this.cancel('test-failure');
  }
}
