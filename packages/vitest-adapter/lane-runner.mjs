import { VitestTestRunner } from 'vitest/runners';

// bail must remain zero: stock bail cancels every project in the controller.
export default class LaneRunner extends VitestTestRunner {
  onAfterRunTask(task) {
    super.onAfterRunTask(task);
    if (task.result?.state === 'fail') this.cancel('test-failure');
  }
}
