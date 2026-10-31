//! One broad /proc discovery, with pure projections for each process tree.
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
};

pub fn identity(pid: u32) -> Result<Option<(u32, u64)>, String> {
    let stat = match fs::read_to_string(format!("/proc/{pid}/stat")) {
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

/// Children of every thread of `pid`; an exited process or thread has none.
fn children(pid: u32) -> Result<Vec<u32>, String> {
    let gone = |error: &std::io::Error| {
        error.kind() == std::io::ErrorKind::NotFound || error.raw_os_error() == Some(libc::ESRCH)
    };
    let tasks = match fs::read_dir(format!("/proc/{pid}/task")) {
        Ok(tasks) => tasks,
        Err(error) if gone(&error) => return Ok(vec![]),
        Err(error) => return Err(format!("process tasks unavailable for PID {pid}: {error}")),
    };
    let mut result = vec![];
    for task in tasks {
        let task = match task {
            Ok(task) => task,
            Err(error) if gone(&error) => continue,
            Err(error) => return Err(format!("process task unavailable for PID {pid}: {error}")),
        };
        match fs::read_to_string(task.path().join("children")) {
            Ok(list) => {
                for child in list.split_whitespace() {
                    result.push(
                        child
                            .parse()
                            .map_err(|_| format!("invalid children list for PID {pid}"))?,
                    );
                }
            }
            Err(error) if gone(&error) => {}
            Err(error) => {
                return Err(format!(
                    "process children unavailable for PID {pid}: {error}"
                ));
            }
        }
    }
    Ok(result)
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
    /// links (CONFIG_PROC_CHILDREN); callers then use broad discovery. Cleanup
    /// verification must keep using broad discovery.
    pub fn read_tree(root: u32) -> Result<Option<Self>, String> {
        if fs::metadata(format!("/proc/{root}/task/{root}/children")).is_err() {
            return Ok(None);
        }
        Self::walk(root, children, identity).map(Some)
    }

    pub(super) fn walk(
        root: u32,
        mut children: impl FnMut(u32) -> Result<Vec<u32>, String>,
        mut read_identity: impl FnMut(u32) -> Result<Option<(u32, u64)>, String>,
    ) -> Result<Self, String> {
        let mut processes = BTreeMap::new();
        let mut seen = BTreeSet::from([root]);
        let mut queue = VecDeque::new();
        // Like broad discovery, member stat failures are skipped here; roots
        // and members get independent, strict accounting reads later.
        if let Ok(Some(identity)) = read_identity(root) {
            processes.insert(root, identity);
            queue.push_back(root);
        }
        while let Some(pid) = queue.pop_front() {
            for child in children(pid)? {
                if seen.insert(child)
                    && let Ok(Some(identity)) = read_identity(child)
                {
                    processes.insert(child, identity);
                    queue.push_back(child);
                }
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

    #[test]
    fn tree_walk_follows_links_skips_exits_and_matches_broad_projection() {
        // 10 is the subreaper root; 40 was adopted after its parent exited;
        // 32 exits before its stat is read; 90 is an unrelated host process.
        let links = BTreeMap::from([
            (10, vec![20, 30, 40]),
            (20, vec![15]),
            (30, vec![31, 32, 31]),
            (90, vec![91]),
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
        let mut reads = vec![];
        let table = ProcessTable::walk(
            10,
            |pid| Ok(links.get(&pid).cloned().unwrap_or_default()),
            |pid| {
                reads.push(pid);
                Ok(stats.get(&pid).copied())
            },
        )
        .unwrap();
        // Only the owned tree is read, each process once.
        reads.sort_unstable();
        assert_eq!(reads, vec![10, 15, 20, 30, 31, 32, 40]);
        assert_eq!(table.start(10), Some(100));
        let broad = ProcessTable::collect(stats.keys().map(|&pid| Ok(Some(pid))), |pid| {
            Ok(stats.get(&pid).copied())
        })
        .unwrap();
        for root in [10, 20, 30, 40] {
            assert_eq!(table.descendants(root), broad.descendants(root));
        }
    }

    #[test]
    fn tree_walk_propagates_link_errors_and_handles_an_exited_root() {
        assert!(
            ProcessTable::walk(
                1,
                |_| Err("children unreadable".into()),
                |_| Ok(Some((0, 1)))
            )
            .is_err()
        );
        let table = ProcessTable::walk(1, |_| Ok(vec![2]), |_| Ok(None)).unwrap();
        assert!(table.0.is_empty());
    }

    #[test]
    #[cfg(any(target_os = "linux", target_os = "android"))]
    fn tree_walk_finds_real_descendants_when_the_kernel_links_children() {
        let mut child = std::process::Command::new("sh")
            .args(["-c", "sleep 30 & sleep 30 & wait"])
            .spawn()
            .unwrap();
        let root = std::process::id();
        let mut found = None;
        for _ in 0..200 {
            match ProcessTable::read_tree(root).unwrap() {
                None => break, // Kernel without CONFIG_PROC_CHILDREN.
                Some(table) => {
                    let owned = table.descendants(root);
                    if owned.len() >= 3 {
                        found = Some(owned);
                        break;
                    }
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let broad = ProcessTable::read_for_cleanup().descendants(root);
        let _ = child.kill();
        let _ = child.wait();
        // Hosted CI kernels link children; only local sandboxes may lack them.
        if std::env::var_os("CI").is_some() {
            assert!(found.is_some(), "CI kernel must expose children links");
        }
        if let Some(owned) = found {
            assert!(owned.contains_key(&child.id()));
            for (pid, start) in &owned {
                assert_eq!(
                    broad.get(pid),
                    Some(start),
                    "walk saw {pid} that broad missed"
                );
            }
        }
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
