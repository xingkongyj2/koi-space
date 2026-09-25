from __future__ import annotations
import re
from . import protocol
class Validator:
 def action(self,before,after,action):
  ok=action.kind in {'wait','scroll'} or before.url!=after.url or before.snapshot!=after.snapshot
  protocol.log(f'flow=validate action={action.kind} status={"passed" if ok else "noop"}')
  return ok
 def step(self,obs,criteria,start_url=''):
  text=(obs.url+'\n'+obs.snapshot).lower()
  if start_url and not obs.url.startswith(start_url.rstrip('/')): return False
  for c in criteria:
   c=c.strip();
   if not c or c.startswith('页面'): continue
   if c.lower().startswith('url contains:') and c[13:].strip().lower() not in obs.url.lower(): return False
   if c.lower() not in text and not re.search(c,text,re.I): return False
  return True
