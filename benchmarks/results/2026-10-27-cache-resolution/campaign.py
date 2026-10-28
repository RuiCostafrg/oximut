import os,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[3]; out=root/'.oximut/cache-resolution';out.mkdir(parents=True, exist_ok=False)
env=os.environ.copy();env['PATH']='/root/.local/share/oximut/toolchains/node-v26.3.1-linux-arm64/bin:'+env['PATH']
def run(label,binary,policy,fixture='m1',repetitions=1,warmups=0,changes=()):
 cmd=['python3','benchmarks/m6.py','--binary',binary,'--fixtures',fixture,'--tools','oximut','--scenarios','incremental','--cache-policy',policy,'--changes',*changes,'--repetitions',str(repetitions),'--warmups',str(warmups),'--output',str(out/label)]
 print(time.strftime('%H:%M:%S'),label,flush=True)
 with (out/(label+'.log')).open('w') as log:subprocess.run(cmd,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
for i in range(6):
 label='warmup' if i==0 else f'measured-{i:02}'
 for side in (['before','after'] if i%2==0 else ['after','before']):
  run(f'{label}-{side}', '/tmp/oximut-sha-before' if side=='before' else '/tmp/oximut-cache-auto','legacy' if side=='before' else 'always')
run('m1-auto','/tmp/oximut-cache-auto','auto',repetitions=5,warmups=1)
run('pure-auto','/tmp/oximut-cache-auto','auto',fixture='pure',repetitions=5,warmups=1)
run('input-invalidation','/tmp/oximut-cache-auto','always',changes=('source','test','helper','snapshot','config','lockfile','environment'))
print('ALL FINAL GATES PASSED',flush=True)
