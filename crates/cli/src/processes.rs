//! One broad /proc discovery, with pure projections for each process tree.
use std::{
    collections::{BTreeMap, BTreeSet},
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
