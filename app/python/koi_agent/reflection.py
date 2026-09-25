from __future__ import annotations
from . import protocol
class Reflection:
 def __init__(self,model=None): self.model=model
 def advise(self,goal,observation,error=''):
  protocol.log(f'flow=reflection goal={goal[:80]!r} error={error[:120]!r}')
  if self.model:
   try:return self.model(goal,observation.snapshot,error)
   except Exception as exc: protocol.log(f'flow=reflection failed={exc}')
  return '重新观察页面，只使用当前 snapshot 中仍存在的元素引用。'
