import fs from 'node:fs';
import { randomUUID } from 'node:crypto';

const realm = globalThis.__oximutProbeRealm ??= randomUUID();
fs.appendFileSync(process.env.OXIMUT_PREWARM_OBSERVATIONS, JSON.stringify({
  phase: 'setup-file', requestedId: process.env.OXIMUT_PROBE_JOB_ID,
  activeMutant: process.env.OXIMUT_ACTIVE_MUTANT ?? null, pid: process.pid, realm,
}) + '\n');
