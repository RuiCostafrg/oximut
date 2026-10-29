# Initial native matched campaign — invalid global gate

Run: https://github.com/RuiCostafrg/oximut/actions/runs/40564773542
Head: `5443556d8f4d1afbce1a5c968bdf16052bff64e1`; tested PR merge revision and
exact executable digest are retained in `metadata.json`. Native Ubuntu 24.04
x86_64 hosted runner, pinned tools, 8/64 arithmetic mutants/tests, one warmup and
five measured alternating-order rounds, no outcome caching.

**The global correctness/measurement gate failed. No performance target is
certified by this campaign.** One fresh-reference sample hit an ordinary process
exit (`ESRCH`) while rechecking `/proc/PID/stat` identity. The command itself
exited 0, but the harness correctly rejected its incomplete memory evidence.
The follow-up sampler fix treats ESRCH in that identity check as a vanished
process, consistent with the existing smaps exit-race handling. These raw files
remain unchanged; a new complete campaign is required.

The complete selected-mode/Stryker pairs point to missed goals: initial paired
median wall ratios about 1.04 (small) / 2.55 (scale), RSS ratios about 0.76 / 0.87,
and CPU ratios about 1.11 / 2.74. Those are diagnostic observations from an invalid
global campaign, not certified product comparisons or evidence of a speedup.
All raw attempts and failures are retained, including the failed reference.

The scaled Oximut report records eight disabled-switch validation baselines in
addition to the original baseline and 64 selected mutation jobs. These checks
and fresh test forks preserve isolation; they contribute real execution work.
The observed process count is cumulative, not peak simultaneous processes or
worker threads. Stryker reuses its runner differently. No isolation check was
disabled or mutant scope reduced to improve the result. Future performance work
must preserve correctness and publish a new campaign.
