"""Inspect retained evidence only; no product execution or scoring."""
from pathlib import Path
import collections,gzip,hashlib,json,subprocess
ROOT=Path('/tmp/oximut-w13-score-43669808238/raw/raw/colord-scale-4w/round-0')
DEST=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text());sha=lambda b:hashlib.sha256(b).hexdigest()
failed=read(ROOT/'after/oximut/report.json');stock=read(ROOT/'before/oximut/report.json')
events=[json.loads(l) for l in (ROOT/'after/oximut/prepared/integration-events.jsonl').read_text().splitlines()]
installed={r['path']:r['sha256'] for r in json.loads(gzip.decompress((ROOT/'after/oximut/prepared/validation-files.json.gz').read_bytes()))}
chunk='node_modules/vitest/dist/chunks/index.Di8au7q4.js'
assert sha(Path(chunk).read_bytes())==installed['/home/runner/work/oximut/oximut/'+chunk]
controllers=[]
for pid in dict.fromkeys(e['controller'] for e in events):
 rows=[(i,e) for i,e in enumerate(events) if e['controller']==pid]
 controllers.append({'pid':pid,'event_range':[rows[0][0],rows[-1][0]],'counts':dict(collections.Counter(e['type'] for _,e in rows)),'tail':[(i,e) for i,e in rows[-12:]]})
old={m['id']:m for m in stock['mutants']};bad=[]
for i,m in enumerate(failed['mutants']):
 if m['outcome']!='infrastructure-error':continue
 bad.append({'report_index':i,'id':m['id'],'source':m['file'],'location':m['location'],'candidate_outcome':m['outcome'],'stock_outcome':old[m['id']]['outcome'],'errors':m['errors'],'tests':m['tests'],'trace':[(i,e) for i,e in enumerate(events) if e.get('selector')==m['id']]})
assert len(bad)==4 and sum(e['type']=='fork' for e in events)==205
sources={}
for name,rev,start,end in [('shared-lanes.ts','88b7d6712648979dc7438a4044abf0f63650d463',330,374),('coordinator.rs','5dfd17e53cd7b067586b3ad47363cb3ecee5873e',465,477)]:
 path='packages/vitest-adapter/'+name if name.endswith('.ts') else 'crates/cli/src/'+name
 data=subprocess.check_output(['git','show',rev+':'+path]);sources[path]={'revision':rev,'sha256':sha(data),'excerpt_start':start,'excerpt':data.decode().splitlines()[start-1:end]}
source=Path(chunk).read_text();sources[chunk]={'sha256':sha(source.encode()),'matches_retained_installed_inventory':True,'excerpts':{f'{a}-{b}':source.splitlines()[a-1:b] for a,b in [(5253,5275),(5489,5511),(5929,5940)]}}
record={'original_run':43669808238,'original_source':'88b7d6712648979dc7438a4044abf0f63650d463','status':'Original cause not recoverable offline; generic aggregate batch outcome lost nested cause','controllers':controllers,'four_mutants':bad,'inspected_sources':sources,'limits':['ForksPoolWorker.start creates child without awaiting readiness; PoolRunner later awaits start response. This does not prove a race in the original run.','Pool.schedule wraps start rejection with Error.cause. SharedLanes emits generic unattributed text instead of serializing result.unhandledErrors.','Controller stdout/stderr are buffered in memory. diagnostics() is returned for controller initialization errors, not ordinary Result replies; these batches were ordinary Result replies. Retained command output has no original aggregate error/stack.','Configured206/recorded205 does not prove whether a child was never created, exited, failed identity inspection, or failed elsewhere before the fork event.']}
(DEST/'offline-audit.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'status':record['status'],'controller_counts':[{k:r[k] for k in ['pid','counts']} for r in controllers],'bad_report_indices':[r['report_index'] for r in bad]},indent=2))
