"""Offline evidence projection; does not execute a worker or benchmark."""
import hashlib,json,pathlib,sys
artifact,positive,negative=map(pathlib.Path,sys.argv[1:4])
a=json.loads((artifact/'build.json').read_text())
trace=[json.loads(x) for x in next(positive.glob('trace-*.jsonl')).read_text().splitlines()]
loads=[x for x in trace if x['phase']=='load']
from urllib.parse import urlparse,unquote
source_urls={'file://'+x:x for x in a['selected']}
facade_urls=set(a['facades'].values())
loaded={x['url'] for x in loads}
identity=json.loads((positive/'identity.json').read_text())
result=json.loads((negative/'result.json').read_text())
error=result['reports'][0]['tests'][0]['errors'][0]
framework_outputs=[x for x in a['outputs'] if x.get('modules') and any(p in a['selected'] for p in x['modules'])]
report={
 'classification':'NO-GO: source-frame fidelity failure; work-removal requirement unestablished',
 'positive':identity,
 'negative':{'expectedFailure':True,'errorMessage':error['message'].splitlines()[0],'stack':error['stack']},
 'sourceFrameFailure':{'observedEmittedFrame':'file://'+str(artifact/'bundle/owner-19.mjs'),'requiredOriginal':a['owners']['test']},
 'topology':{'selectedOriginals':len(a['selected']),'frameworkOutputs':len(framework_outputs),'modulesPerFrameworkOutput':[len(x['modules']) for x in framework_outputs],'extraRuntimeOutputs':[x['path'] for x in a['outputs'] if x.get('modules') and not any(p in a['selected'] for p in x['modules'])]},
 'executed':{'originalSelectedNativeLoads':sorted(loaded & set(source_urls)), 'facadeNativeLoads':sorted(loaded & facade_urls),'instrumentedEvaluations':len(identity['evaluations']), 'loadCount':len(loads)},
 'resolverConditions':sorted({tuple(x.get('conditions',[])) for x in trace if x['phase']=='resolve'}),
 'notProven':['source-frame fidelity','inactive/A/B/A outcomes and inventory','fresh realm across jobs','hooks coverage beyond actual test attribution','importActual and mock identities','fake clocks','live environment/cwd/argv/randomness','import-time throws','deadlines beyond harness and descendant matrix','no added eager evaluation per ingress','alternate installation/custom loader/evaluator rejection','complete runtime external closure attestation','structural loading work reduction','performance targets'],
}
assert not report['executed']['originalSelectedNativeLoads']
assert all(x==1 for x in identity['evaluations'].values())
assert len(framework_outputs)==len(a['selected'])
assert all(len(x['modules'])==1 for x in framework_outputs)
assert str(artifact/'bundle/owner-19.mjs') in error['stack']
print(json.dumps(report,indent=2))
