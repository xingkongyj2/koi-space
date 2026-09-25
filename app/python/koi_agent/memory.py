from __future__ import annotations
import json,time
from pathlib import Path
class Memory:
 def __init__(self,path=None): self.path=Path(path).expanduser() if path else None
 def write(self,kind,data):
  if not self.path:return
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.path.open('a',encoding='utf8') as f:f.write(json.dumps({'kind':kind,'time':time.time(),'data':data},ensure_ascii=False)+'\n')
 def search(self,query,limit=5):
  if not self.path or not self.path.exists(): return []
  terms=set(query.lower().split()); hits=[]
  for line in self.path.read_text(encoding='utf8').splitlines():
   try: item=json.loads(line); text=json.dumps(item,ensure_ascii=False).lower(); score=sum(t in text for t in terms)
   except ValueError: continue
   if score: hits.append((score,item))
  return [item for _,item in sorted(hits,key=lambda x:x[0],reverse=True)[:limit]]
 def record_success(self,plan,steps): self.write('success',{'goals':[s.goal for s in plan.steps],'steps':steps})
 def record_failure(self,reason): self.write('failure',{'reason':reason})
