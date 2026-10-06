"""Small real-model outline check. Runs on a hosted Mac; this is not a production latency benchmark."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_notes import apply_patch
from host.setup_server import download

root=Path(__file__).resolve().parents[1]
model=next(m for m in json.loads((root/'host/models.json').read_text())if m['id']=='sermon-notes')
for item in model['files']:download(item,root/'models/sermon-notes'/item['name'])
sources=[{'id':'s1','line':1,'text':"God has not forgotten you. God's love does not depend on your performance. You do not have to earn his grace."},{'id':'s2','line':2,'text':'Relationships require patience. We need to listen before we speak, and treat people with respect.'}]
document={'title':'Private verification fixture','points':[],'translations':{'es':{'points':[]},'ko':{'points':[]}},'sources':sources}
process=subprocess.Popen([sys.executable,str(root/'live_notes_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env={**os.environ,'GATHER_NOTES_CPU':'0'})
results=[]
try:
    for source in sources:
        process.stdin.write(json.dumps({'sources':[source],'headings':[p['title']for p in document['points']]})+'\n');process.stdin.flush()
        result=json.loads(process.stdout.readline())
        if 'error'in result:raise RuntimeError(result['error'])
        apply_patch(document,result['patch'],[source]);results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    assert document['processed_line']==2
    assert len(document['points'])==2,'The model should distinguish grace from human relationships.'
    assert all('s'+str(i+1)in result['patch']['source_ids']for i,result in enumerate(results))
    output=root/'proof/live-notes-model-check.json';output.write_text(json.dumps({'scope':'Two synthetic sources; real Qwen3 4B generation on GitHub hosted Mac. Not a production latency, full-sermon, or native-speaker assessment.','results':results,'document':document},ensure_ascii=False,indent=2))
finally:
    process.terminate();process.wait(timeout=10)
