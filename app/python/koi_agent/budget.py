from __future__ import annotations
import time
from dataclasses import dataclass
@dataclass
class BudgetSnapshot:
 steps:int; failures:int; elapsed:float; remaining_steps:int; tokens:int
@dataclass
class Budget:
 max_steps:int=30; max_failures:int=3; max_seconds:float=300
 started:float=0; steps:int=0; failures:int=0; tokens:int=0
 def __post_init__(self): self.started=time.monotonic()
 def consume_step(self): self.steps+=1
 def failure(self): self.failures+=1
 def add_tokens(self,count:int): self.tokens += max(0,count)
 def snapshot(self): return BudgetSnapshot(self.steps,self.failures,time.monotonic()-self.started,max(0,self.max_steps-self.steps),self.tokens)
 def exhausted(self): return self.steps>=self.max_steps or self.failures>=self.max_failures or time.monotonic()-self.started>=self.max_seconds
 def allow(self): return not self.exhausted()
