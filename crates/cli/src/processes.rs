//! One broad /proc discovery, with pure projections for each process tree.
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
};

/// Reads a pseudo-file without querying its reported size (usually zero).
/// Continue through short reads until EOF: a short record is not proof that
/// a procfs/seq_file or cgroup stream has ended.
pub fn read_proc(path: impl AsRef<std::path::Path>) -> std::io::Result<String> {
    read_record(fs::File::open(path)?)
}

fn read_record(mut reader: impl std::io::Read) -> std::io::Result<String> {
    let mut bytes = Vec::with_capacity(4096);
    reader.read_to_end(&mut bytes)?;
    String::from_utf8(bytes).map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "stream did not contain valid UTF-8",
        )
    })
}

pub fn identity(pid: u32) -> Result<Option<(u32, u64)>, String> {
    let stat = match read_proc(format!("/proc/{pid}/stat")) {
        Ok(stat) => stat,
        Err(error)
            if error.kind() == std::io::ErrorKind::NotFound
                || error.raw_os_error() == Some(libc::ESRCH) =>
        {
            return Ok(None);
        }
        Err(error) => {
            return Err(format!(
                "process identity unavailable for PID {pid}: {error}"
            ));
        }
    };
    parse_identity(&stat)
        .map(Some)
        .ok_or_else(|| format!("invalid process identity for PID {pid}"))
}

fn parse_identity(stat: &str) -> Option<(u32, u64)> {
    let mut fields = stat.rsplit_once(')')?.1.split_whitespace();
    let parent = fields.nth(1)?.parse().ok()?;
    let start = fields.nth(17)?.parse().ok()?;
    Some((parent, start))
}

fn gone(error: &std::io::Error) -> bool {
    error.kind() == std::io::ErrorKind::NotFound || error.raw_os_error() == Some(libc::ESRCH)
}

/// Children of every thread of `pid`, or `None` when the process is gone. An
/// exited (zombie) process has already handed its children to a subreaper.
fn children(pid: u32) -> Result<Option<Vec<u32>>, String> {
    match read_proc(format!("/proc/{pid}/stat")) {
        Ok(stat) => {
            let state = stat
                .rsplit_once(')')
                .and_then(|(_, fields)| fields.split_whitespace().next())
                .ok_or_else(|| format!("invalid process state for PID {pid}"))?;
            if matches!(state, "Z" | "X" | "x") {
                return Ok(None);
            }
        }
        Err(error) if gone(&error) => return Ok(None),
        Err(error) => return Err(format!("process state unavailable for PID {pid}: {error}")),
    }
    let tasks = match fs::read_dir(format!("/proc/{pid}/task")) {
        Ok(tasks) => tasks,
        Err(error) if gone(&error) => return Ok(None),
        Err(error) => return Err(format!("process tasks unavailable for PID {pid}: {error}")),
    };
    // Read lazily: one thread's list is held at a time. A vanished task entry
    // is reported like a vanished children file (the tree changed).
    parse_children(
        pid,
        tasks.map(|task| task.and_then(|task| read_task_children(&task.path()))),
    )
}

/// One thread's `children` list. A thread that exits after the file opens can
/// read as empty; if its task entry is gone after the read, report it as
/// vanished. A later exit is harmless: this list was already captured.
fn read_task_children(task: &std::path::Path) -> std::io::Result<String> {
    let list = read_proc(task.join("children"))?;
    fs::metadata(task.join("stat"))?;
    Ok(list)
}

/// Combines the per-thread `children` lists. A thread that vanished mid-read
/// handed its children to a sibling thread that may already have been read,
/// so the tree changed: report it (`None`) instead of a partial list.
fn parse_children(
    pid: u32,
    lists: impl IntoIterator<Item = std::io::Result<String>>,
) -> Result<Option<Vec<u32>>, String> {
    let mut result = vec![];
    for list in lists {
        match list {
            Ok(list) => {
                for child in list.split_whitespace() {
                    result.push(
                        child
                            .parse()
                            .map_err(|_| format!("invalid children list for PID {pid}"))?,
                    );
                }
            }
            Err(error) if gone(&error) => return Ok(None),
            Err(error) => {
                return Err(format!(
                    "process children unavailable for PID {pid}: {error}"
                ));
            }
        }
    }
    Ok(Some(result))
}

#[derive(Default)]
pub struct ProcessTable(BTreeMap<u32, (u32, u64)>);

impl ProcessTable {
    pub fn read_for_cleanup() -> Self {
        // Match cleanup's existing best-effort directory/stat handling. Never
        // retain this table between calls or reuse an accounting sample to kill.
        let Ok(entries) = fs::read_dir("/proc") else {
            return Self::default();
        };
        let pids = entries
            .flatten()
            .filter_map(|entry| entry.file_name().to_string_lossy().parse::<u32>().ok())
            .map(|pid| Ok(Some(pid)));
        Self::collect(pids, identity).unwrap_or_default()
    }

    pub fn read_with(
        read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    ) -> Result<Self, String> {
        let entries = fs::read_dir("/proc")
            .map_err(|error| format!("process accounting unavailable: {error}"))?;
        let pids = entries.map(|entry| {
            entry
                .map(|entry| entry.file_name().to_string_lossy().parse::<u32>().ok())
                .map_err(|error| format!("process directory entry unavailable: {error}"))
        });
        Self::collect(pids, read_identity)
    }

    pub(super) fn collect(
        pids: impl IntoIterator<Item = Result<Option<u32>, String>>,
        mut read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    ) -> Result<Self, String> {
        let mut processes = BTreeMap::new();
        for pid in pids {
            if let Some(pid) = pid?
                // Broad discovery includes unrelated host processes. Preserve
                // best-effort stat handling here; selected roots and discovered
                // members get independent, strict accounting reads later.
                && let Ok(Some(identity)) = read_identity(pid)
            {
                processes.insert(pid, identity);
            }
        }
        Ok(Self(processes))
    }

    /// Periodic accounting of an owned tree only: follow every thread's
    /// `children` links from `root` instead of reading every host process.
    /// Orphans are reparented to the subreaper `root` (or a subreaper below
    /// it), so they stay reachable. `Ok(None)` means the kernel lacks these
    /// links (CONFIG_PROC_CHILDREN). Any sign that the tree changed during the
    /// walk is an error; callers then use broad discovery for that sample.
    /// Cleanup verification and forced samples always use broad discovery.
    pub fn read_tree(root: u32) -> Result<Option<Self>, String> {
        if fs::metadata(format!("/proc/{root}/task/{root}/children")).is_err() {
            return Ok(None);
        }
        Self::walk(root, children, identity).map(Some)
    }

    /// `children` returns `Ok(None)` when the process itself is gone.
    pub(super) fn walk(
        root: u32,
        mut children: impl FnMut(u32) -> Result<Option<Vec<u32>>, String>,
        mut read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    ) -> Result<Self, String> {
        let changed = |pid: u32| format!("owned tree changed during walk at PID {pid}");
        let mut processes = BTreeMap::new();
        let root_identity = read_identity(root)?.ok_or_else(|| changed(root))?;
        processes.insert(root, root_identity);
        let mut queue = VecDeque::from([root]);
        while let Some(pid) = queue.pop_front() {
            // An exit here reparents its children to an already-visited
            // subreaper, which this walk would miss.
            for child in children(pid)?.ok_or_else(|| changed(pid))? {
                if processes.contains_key(&child) {
                    continue;
                }
                // A listed PID that exited, or now names a process whose
                // parent is outside the tree (reuse or reparenting), means the
                // walk raced with the tree.
                let (parent, start) = read_identity(child)?.ok_or_else(|| changed(child))?;
                if parent != pid && !processes.contains_key(&parent) {
                    return Err(changed(child));
                }
                processes.insert(child, (parent, start));
                queue.push_back(child);
            }
        }
        Ok(Self(processes))
    }

    pub fn start(&self, pid: u32) -> Option<u64> {
        self.0.get(&pid).map(|&(_, start)| start)
    }

    pub fn descendants(&self, parent: u32) -> BTreeMap<u32, u64> {
        let mut owned = BTreeSet::from([parent]);
        loop {
            let before = owned.len();
            for (&pid, &(ppid, _)) in &self.0 {
                if owned.contains(&ppid) {
                    owned.insert(pid);
                }
            }
            if before == owned.len() {
                break;
            }
        }
        owned.remove(&parent);
        self.0
            .iter()
            .filter(|(pid, _)| owned.contains(pid))
            .map(|(&pid, &(_, start))| (pid, start))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pseudo_file_reads_continue_after_short_reads_and_interruptions() {
        struct ShortReads {
            interrupted: bool,
            remaining: &'static [u8],
        }
        impl std::io::Read for ShortReads {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                if !self.interrupted {
                    self.interrupted = true;
                    return Err(std::io::ErrorKind::Interrupted.into());
                }
                let count = buffer.len().min(self.remaining.len()).min(3);
                buffer[..count].copy_from_slice(&self.remaining[..count]);
                self.remaining = &self.remaining[count..];
                Ok(count)
            }
        }
        assert_eq!(
            read_record(ShortReads {
                interrupted: false,
                remaining: b"123 456 789 1234",
            })
            .unwrap(),
            "123 456 789 1234"
        );
        struct FailedRead;
        impl std::io::Read for FailedRead {
            fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
                Err(std::io::ErrorKind::PermissionDenied.into())
            }
        }
        assert_eq!(
            read_record(FailedRead).unwrap_err().kind(),
            std::io::ErrorKind::PermissionDenied
        );
    }

    #[test]
    fn read_proc_returns_whole_files_of_every_size_like_read_to_string() {
        let dir = tempfile::tempdir().unwrap();
        for size in [0usize, 1, 4095, 4096, 4097, 8192, 20_000] {
            let path = dir.path().join(format!("f{size}"));
            let text: String = (0..size)
                .map(|i| char::from(b'a' + (i % 26) as u8))
                .collect();
            fs::write(&path, &text).unwrap();
            assert_eq!(read_proc(&path).unwrap(), text, "{size} bytes");
        }
        let multibyte = "é".repeat(2049); // a character straddles the first chunk
        fs::write(dir.path().join("m"), &multibyte).unwrap();
        assert_eq!(read_proc(dir.path().join("m")).unwrap(), multibyte);
        fs::write(dir.path().join("bad"), [0xff, 0xfe]).unwrap();
        assert_eq!(
            read_proc(dir.path().join("bad")).unwrap_err().kind(),
            std::io::ErrorKind::InvalidData
        );
        assert_eq!(
            read_proc(dir.path().join("none")).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        #[cfg(any(target_os = "linux", target_os = "android"))]
        {
            let me = std::process::id();
            let stat = read_proc(format!("/proc/{me}/stat")).unwrap();
            assert!(stat.starts_with(&format!("{me} (")), "{stat}");
            assert_eq!(
                parse_identity(&stat).map(|(parent, _)| parent),
                identity(me).unwrap().map(|(parent, _)| parent)
            );
            let meminfo = read_proc("/proc/meminfo").unwrap();
            assert!(meminfo.len() > 1024 && meminfo.contains("MemAvailable"));
        }
    }

    #[test]
    fn one_collection_projects_nested_sibling_unrelated_and_adopted_trees() {
        // A grandchild sorts before its parent. PID 40 was adopted by the CLI
        // after its worker exited; it must remain in aggregate accounting.
        let data = BTreeMap::from([
            (10, (1, 100)),
            (20, (10, 200)),
            (15, (20, 300)),
            (30, (10, 400)),
            (31, (30, 500)),
            (40, (10, 600)),
            (41, (40, 700)),
            (90, (1, 800)),
            (91, (90, 900)),
        ]);
        let mut reads = 0;
        let table = ProcessTable::collect(data.keys().map(|&pid| Ok(Some(pid))), |pid| {
            reads += 1;
            Ok(data.get(&pid).copied())
        })
        .unwrap();
        assert_eq!(reads, data.len());
        assert_eq!(
            table.descendants(10),
            BTreeMap::from([
                (15, 300),
                (20, 200),
                (30, 400),
                (31, 500),
                (40, 600),
                (41, 700),
            ])
        );
        assert_eq!(table.descendants(20), BTreeMap::from([(15, 300)]));
        assert_eq!(table.descendants(30), BTreeMap::from([(31, 500)]));
        assert_eq!(table.descendants(15), BTreeMap::new());
        assert_eq!(table.descendants(99), BTreeMap::new());
        assert_eq!(reads, data.len(), "projections must never read /proc");
    }

    type Links = BTreeMap<u32, Option<Vec<u32>>>;
    type Stats = BTreeMap<u32, (u32, u64)>;

    fn walk(links: &Links, stats: &Stats, reads: &mut Vec<u32>) -> Result<ProcessTable, String> {
        ProcessTable::walk(
            10,
            |pid| Ok(links.get(&pid).cloned().unwrap_or(Some(vec![]))),
            |pid| {
                reads.push(pid);
                Ok(stats.get(&pid).copied())
            },
        )
    }

    fn tree() -> (Links, Stats) {
        // 10 is the subreaper root; 40 was adopted by it after its parent
        // exited; 90 is an unrelated host process.
        let links = BTreeMap::from([
            (10, Some(vec![20, 30, 40])),
            (20, Some(vec![15])),
            (30, Some(vec![31, 31])),
            (90, Some(vec![91])),
        ]);
        let stats = BTreeMap::from([
            (10, (1, 100)),
            (20, (10, 200)),
            (15, (20, 300)),
            (30, (10, 400)),
            (31, (30, 500)),
            (40, (10, 600)),
            (90, (1, 800)),
            (91, (90, 900)),
        ]);
        (links, stats)
    }

    #[test]
    fn tree_walk_reads_only_the_owned_tree_and_matches_broad_projection() {
        let (links, stats) = tree();
        let mut reads = vec![];
        let table = walk(&links, &stats, &mut reads).unwrap();
        reads.sort_unstable();
        assert_eq!(
            reads,
            vec![10, 15, 20, 30, 31, 40],
            "each owned process once"
        );
        let broad = ProcessTable::collect(stats.keys().map(|&pid| Ok(Some(pid))), |pid| {
            Ok(stats.get(&pid).copied())
        })
        .unwrap();
        for root in [10, 20, 30, 40] {
            assert_eq!(table.descendants(root), broad.descendants(root));
        }
        // A child already reparented to an owned subreaper is still owned.
        let (mut links, mut stats) = tree();
        links.insert(20, Some(vec![15, 16]));
        stats.insert(16, (10, 350));
        let table = walk(&links, &stats, &mut vec![]).unwrap();
        assert_eq!(table.descendants(10).get(&16), Some(&350));
    }

    #[test]
    fn tree_walk_refuses_partial_tables_when_the_tree_changes() {
        type Change = Box<dyn Fn(&mut Links, &mut Stats)>;
        let cases: Vec<(&str, Change)> = vec![
            (
                "root gone",
                Box::new(|_, stats| {
                    stats.remove(&10);
                }),
            ),
            (
                "listed child exited",
                Box::new(|_, stats| {
                    stats.remove(&31);
                }),
            ),
            (
                "visited process exited",
                Box::new(|links, _| {
                    links.insert(30, None);
                }),
            ),
            (
                "reused PID with a foreign parent",
                Box::new(|_, stats| {
                    stats.insert(15, (90, 999));
                }),
            ),
        ];
        for (name, mutate) in cases {
            let (mut links, mut stats) = tree();
            mutate(&mut links, &mut stats);
            let error = walk(&links, &stats, &mut vec![]).map(|_| ()).unwrap_err();
            assert!(error.contains("changed during walk"), "{name}: {error}");
        }
        assert!(
            ProcessTable::walk(
                1,
                |_| Err("children unreadable".into()),
                |_| Ok(Some((0, 1)))
            )
            .is_err()
        );
        assert!(ProcessTable::walk(1, |_| Ok(Some(vec![])), |_| Err("stat".into())).is_err());
    }

    #[test]
    #[cfg(any(target_os = "linux", target_os = "android"))]
    fn tree_walk_finds_real_descendants_when_the_kernel_links_children() {
        use std::os::unix::process::CommandExt;
        let mut child = std::process::Command::new("sh")
            .args(["-c", "sleep 30 & sleep 30 & wait"])
            .process_group(0)
            .spawn()
            .unwrap();
        let group = child.id() as i32;
        struct Reap(i32);
        impl Drop for Reap {
            fn drop(&mut self) {
                unsafe { libc::kill(-self.0, libc::SIGKILL) };
            }
        }
        let _reap = Reap(group);
        let root = std::process::id();
        let mut found = None;
        for _ in 0..200 {
            match ProcessTable::read_tree(root) {
                Ok(None) => break, // Kernel without CONFIG_PROC_CHILDREN.
                // Parallel tests' short-lived children can race the walk.
                Err(_) => {}
                Ok(Some(table)) => {
                    let shell = table.descendants(child.id());
                    if shell.len() >= 2 {
                        found = Some((table.start(child.id()), shell));
                        break;
                    }
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        // Hosted CI kernels link children; only local sandboxes may lack them.
        if std::env::var_os("CI").is_some() {
            assert!(found.is_some(), "CI kernel must expose children links");
        }
        if let Some((start, shell)) = found {
            // Compare only this test's own, still-running subtree.
            let broad = ProcessTable::read_for_cleanup();
            assert_eq!(start, broad.start(child.id()));
            assert_eq!(shell, broad.descendants(child.id()));
        }
        drop(_reap);
        let _ = child.wait();
    }

    #[test]
    fn a_thread_gone_after_reading_its_children_counts_as_vanished() {
        let task = tempfile::tempdir().unwrap();
        fs::write(task.path().join("children"), "").unwrap();
        // The empty read succeeded, but the task entry is gone afterwards.
        let error = read_task_children(task.path()).unwrap_err();
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound);
        assert_eq!(parse_children(1, [Err(error)]).unwrap(), None);
        fs::write(task.path().join("stat"), "1 (x) S").unwrap();
        fs::write(task.path().join("children"), "7 8").unwrap();
        assert_eq!(read_task_children(task.path()).unwrap(), "7 8");
    }

    #[test]
    fn a_thread_vanishing_mid_read_reports_a_changed_tree() {
        use std::io::{Error, ErrorKind};
        let ok = |text: &str| Ok(text.to_string());
        assert_eq!(
            parse_children(1, [ok("4 5 "), ok(""), ok("6")]).unwrap(),
            Some(vec![4, 5, 6])
        );
        // Its children moved to a sibling thread that may already be read.
        assert_eq!(
            parse_children(1, [ok("4"), Err(Error::from(ErrorKind::NotFound))]).unwrap(),
            None
        );
        assert_eq!(
            parse_children(1, [Err(Error::from_raw_os_error(libc::ESRCH))]).unwrap(),
            None
        );
        assert!(parse_children(1, [Err(Error::from(ErrorKind::PermissionDenied))]).is_err());
        assert!(parse_children(1, [ok("4 x")]).is_err());
    }

    #[test]
    fn projection_keeps_children_when_root_exits_and_terminates_on_cycles() {
        let table = ProcessTable(BTreeMap::from([
            (2, (1, 20)),
            (3, (2, 30)),
            (4, (5, 40)),
            (5, (4, 50)),
        ]));
        assert_eq!(table.start(1), None);
        assert_eq!(table.descendants(1), BTreeMap::from([(2, 20), (3, 30)]));
        assert_eq!(table.descendants(4), BTreeMap::from([(5, 50)]));
    }

    #[test]
    fn collection_skips_unreadable_identities_but_propagates_directory_errors() {
        let table = ProcessTable::collect(
            [Ok(None), Ok(Some(1)), Ok(Some(2)), Ok(Some(3))],
            |pid| match pid {
                1 => Ok(None),
                2 => Err("permission denied".into()),
                3 => Ok(Some((0, 30))),
                _ => unreachable!(),
            },
        )
        .unwrap();
        assert_eq!(table.0, BTreeMap::from([(3, (0, 30))]));
        assert!(ProcessTable::collect([Err("directory entry".into())], |_| Ok(None)).is_err());
        assert!(parse_identity("invalid").is_none());
        let mut fields = vec!["0"; 22];
        fields[0] = "S";
        fields[1] = "123";
        fields[19] = "456";
        assert_eq!(
            parse_identity(&format!("42 (name ) with spaces) {}", fields.join(" "))),
            Some((123, 456))
        );
    }
}
