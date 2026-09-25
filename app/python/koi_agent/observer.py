from __future__ import annotations
from dataclasses import dataclass
from . import protocol
@dataclass(frozen=True)
class Observation:
 url:str; title:str; snapshot:str; diff:str; changed:bool; elements:tuple[dict,...]=()
class Observer:
 def __init__(self,session): self.session=session; self._last=''
 def capture(self):
  url=self.session.current_url(); result=self.session.run(['snapshot','-i'],timeout=30)
  snap=result.stdout if result.ok else result.preview; diff=self.diff(self._last,snap); self._last=snap
  protocol.log(f'flow=observe url={url or "<unknown>"} bytes={len(snap)} changed={bool(diff)}')
  elements=tuple(self._elements(snap))
  return Observation(url,'',snap,diff,bool(diff),elements)
 @staticmethod
 def _elements(snapshot):
  """Normalize agent-browser refs into a compact decision table."""
  import re
  for line in snapshot.splitlines():
   m=re.search(r'(@[ref\w-]+)\s+(.+)',line)
   if m: yield {'ref':m.group(1),'text':m.group(2).strip()[:240]}
 @staticmethod
 def diff(old,new):
  if not old:return new[:3000]
  if old==new:return ''
  a,b=old.splitlines(),new.splitlines(); sa,sb=set(a),set(b)
  return '\n'.join(['+'+x for x in sb-sa][:80]+['-'+x for x in sa-sb][:80])
