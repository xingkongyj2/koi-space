from __future__ import annotations
from dataclasses import dataclass
from . import protocol
@dataclass(frozen=True)
class Action:
 kind:str; value:str=''; ref:str=''; expected:str=''; sensitive:bool=False
class Executor:
 ALLOWED={'open','click','fill','press','wait','scroll'}
 def __init__(self,session): self.session=session
 def execute(self,a:Action):
  if a.kind not in self.ALLOWED: raise ValueError(f'unsupported action: {a.kind}')
  if a.sensitive: protocol.notify('此操作需要用户确认后继续','warning'); raise PermissionError('user confirmation required')
  args={'open':['open',a.value],'click':['click',a.ref],'fill':['fill',a.ref,a.value],'press':['press',a.ref,a.value],'wait':['wait',a.value or '500'],'scroll':['scroll',a.value or 'down']}[a.kind]
  protocol.log(f'flow=execute action={a.kind} ref={a.ref or "-"}')
  return self.session.run(args)
