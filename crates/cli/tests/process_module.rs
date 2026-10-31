// Compile the process module under a parent with no process_identity helper.
// This catches accidental dependencies on target-gated binary functions even
// when CI runs on Linux. It does not establish runtime support for other OSes.
#[allow(dead_code)]
#[path = "../src/processes.rs"]
mod processes;

#[cfg(any(target_os = "linux", target_os = "android"))]
#[test]
fn cleanup_discovery_reads_its_own_process_identity() {
    let pid = std::process::id();
    let (_, start) = processes::identity(pid).unwrap().unwrap();
    assert_eq!(
        processes::ProcessTable::read_for_cleanup().start(pid),
        Some(start)
    );
}
