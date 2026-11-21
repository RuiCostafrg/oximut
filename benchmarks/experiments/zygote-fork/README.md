# Zygote fork feasibility

This prototype tests whether one initialized Node process can `fork()` each
job. Each job still gets a fresh OS process, with its own PID and a
copy-on-write address space, without booting Node again. It is Linux-only
and is not wired into Oximut.

```sh
PATH=/path/to/glibc-node/bin:$PATH bash benchmarks/experiments/zygote-fork/run.sh
```

`controller.mjs` checks 12 jobs, at most 4 at a time, and prints a JSON
verdict. One job exits with code 7 and one is killed with SIGTERM. Every
check passed in repeated local runs on Linux aarch64 under PRoot, using
glibc Node 26.3.1. The checks cover:

- distinct PIDs;
- the expected exit codes and signals;
- scavenges plus a forced major GC in every child;
- an async `fs` read and `pbkdf2` on the libuv threadpool;
- timers and promises;
- `advanced` IPC carrying a `Map` and a `BigInt`;
- reaping, with no zombies;
- a clean zygote exit.

`measure.mjs` compares zygote children with fresh `node` processes that run
the same warm preload, which imports Vitest. PRoot timings are not
meaningful, so take these as a sanity check only:

| Arm (median, 16 jobs) | Wall to ready | Child CPU to ready | RSS | PSS |
| --- | ---: | ---: | ---: | ---: |
| Zygote fork | 15–24 ms | 2.4–3.0 ms (+3.8–5 ms in the zygote) | 30 MB | 12 MB |
| Fresh process, same preload | 250–297 ms | 151–161 ms | 57 MB | 25 MB |

## What a forked Node child needs

1. Clear `MADV_DONTFORK` before every fork (`ReenableForkCopy` in `fork.c`).
   V8 marks its heap reservations `dc`, so without this the child has no
   JavaScript heap and segfaults in `InterpreterEntryTrampoline`.
2. Call `uv_loop_fork()` in the child.
3. Start the zygote with `--single-threaded`. Without it, one run in three
   deadlocked on V8 background tasks. `--single-threaded-gc` and
   `--no-concurrent-recompilation` were not needed.
4. Never run Node's normal exit path in the child. `process.exit()` calls
   `DisposePlatform()`, which joins worker threads that did not survive the
   fork and hangs forever. The child replaces `process.reallyExit` with
   `_exit()`, after the JavaScript `exit` handlers have run.
5. Remove inherited zygote listeners. The zygote's `disconnect` handler
   would otherwise exit the child.
6. Redefine `process.pid`, which Node cached at bootstrap.
   `process.ppid` is live and reports the zygote.
7. Create the zygote's end of the socketpair with `manualStart: true`
   before passing it to the controller. A reading socket can consume the
   child's first message.

## Node internals relied on

- `child_process._forkChild(fd, 'advanced')` in the child.
- `internal/child_process.setupChannel` and `pipe_wrap.Pipe` in the
  controller, which needs `--expose-internals`.
- The zygote's IPC handle passing.

## Known risks

- Any thread or lock held at fork time is lost in the child.
  `--single-threaded` stops V8 posting tasks, but Node's platform worker
  threads still exist in the zygote. libuv resets its threadpool through
  `pthread_atfork`.
- Every inherited file descriptor is shared with the zygote, including the
  zygote's own IPC socket until the child disconnects.
- Signal handlers and `process` listeners installed before the fork carry
  over.
- Children are the zygote's children, not the controller's. Exit status
  reaches the controller through the zygote.
- The sampled aggregate RSS counts shared copy-on-write pages in each
  process. PSS shows the actual sharing.
