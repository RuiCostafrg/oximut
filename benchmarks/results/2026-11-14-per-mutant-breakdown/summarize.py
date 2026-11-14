from pathlib import Path
import json,statistics as S
import sys
root=Path(sys.argv[1])
rows=[];batches=[];gaps=[]
for p in sorted((root/'profiles').glob('controller-*.jsonl')):
 events=[json.loads(l) for l in p.read_text().splitlines()]
 reqs=[e for e in events if e['type']=='controller-request' and e.get('kind')=='mutant']
 prev=None
 for r in reqs:
  reply=next(e for e in events if e['type']=='controller-send' and e['id']==r['id'])
  forks=[e for e in events if e['type']=='fork' and r['monotonicMs']<=e['monotonicMs']<reply['monotonicMs']]
  if prev is not None:gaps.append(r['monotonicMs']-prev)
  prev=reply['monotonicMs']
  done_times=[]
  for f in forks:
   ev=[e for e in events if e.get('childPid')==f['childPid']]
   def t(ty,k=None,v=None):return next(e['monotonicMs'] for e in ev if e['type']==ty and (k is None or e.get(k)==v))
   start=t('child-message','messageType','started');run=t('child-send','messageType','run');col=t('child-message','rpcMethod','onCollected');done=t('child-message','messageType','testfileFinished');exit=t('child-exit')
   rows.append(dict(controller=p.stem,pid=f['childPid'],requestToFork=f['monotonicMs']-r['monotonicMs'],forkToStarted=start-f['monotonicMs'],startedToRun=run-start,importAndCollection=col-run,executionAndReport=done-col,teardown=exit-done,finishedToReply=reply['monotonicMs']-done))
   done_times.append(done)
  batches.append(dict(controller=p.stem,lanes=len(forks),requestToReply=reply['monotonicMs']-r['monotonicMs'],lastFinishedToReply=reply['monotonicMs']-max(done_times),laneFinishSkew=max(done_times)-min(done_times)))
summary=dict(scope='Overhead-bearing ARM local diagnostic; independent medians are not additive',mutants=len(rows),batches=len(batches),fork_medians_ms={k:S.median(r[k] for r in rows) for k in rows[0] if k not in ('controller','pid')},batch_medians_ms={k:S.median(r[k] for r in batches) for k in ('requestToReply','lastFinishedToReply','laneFinishSkew')},between_batch_median_ms=S.median(gaps),forks=rows,batch_intervals=batches,between_batch_intervals_ms=gaps)
out=Path(__file__).resolve().parent
(out/'boundary-intervals.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ('forks','batch_intervals','between_batch_intervals_ms')},indent=2))
