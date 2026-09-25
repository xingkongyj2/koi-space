from __future__ import annotations
import json
from dataclasses import dataclass
from .executor import Action
from . import protocol
@dataclass(frozen=True)
class DecisionResult:
 actions:tuple[Action,...]; confidence:float; route:str; rationale:str=''
class Decision:
 def __init__(self,model=None,jev=None): self.model=model; self.jev=jev
 def choose(self,goal,observation,start_url='',slow=False,advice=''):
  if start_url and not observation.url.startswith(start_url.rstrip('/')): return DecisionResult((Action('open',start_url,expected='URL changed'),),1,'rule','start URL')
  if self.jev:
   try:
    value=self.jev.choose(goal,observation.snapshot)
    action=self._jev_action(value); return DecisionResult((action,),float(value.get('confidence',.8)),'jev','typed choice')
   except Exception as exc: protocol.log(f'flow=decision jev_error={exc}')
  if self.model:
   try:
    raw=self.model(json.dumps({'goal':goal,'url':observation.url,'diff':observation.diff,'snapshot':observation.snapshot[:12000],'advice':advice,'slow':slow},ensure_ascii=False))
    obj=json.loads(raw); acts=tuple(Action(str(x['kind']),str(x.get('value','')),str(x.get('ref','')),str(x.get('expected','')),bool(x.get('sensitive'))) for x in obj.get('actions',[])[:10] if x.get('kind') in {'open','click','fill','press','wait','scroll'})
    return DecisionResult(acts,float(obj.get('confidence',.5)),'slow' if slow else 'fast',str(obj.get('rationale','')))
   except Exception as exc: protocol.log(f'flow=decision model_error={exc}')
  return DecisionResult((),0,'none','no safe action available')
 def _jev_action(self,v):
  op=str(v.get('operation',v.get('action','WAIT'))).upper(); ref=str(v.get('target',v.get('target_index','')))
  kinds={'CLICK':'click','TYPE_TEXT':'fill','SELECT':'click','SCROLL':'scroll','WAIT':'wait','DONE':'wait','BLOCKED':'wait'}
  return Action(kinds.get(op,'wait'),str(v.get('text',v.get('value','500'))),ref)
