"""OpenAI-compatible chat client and Jev typed-decision adapter."""
from __future__ import annotations
import json, urllib.request
from .config import Provider
class ModelError(RuntimeError): pass
class OpenAICompatible:
 def __init__(self,p:Provider): self.p=p
 def chat(self,system:str,user:str)->str:
  body=json.dumps({'model':self.p.model,'messages':[{'role':'system','content':system},{'role':'user','content':user}], 'temperature':0, 'response_format':{'type':'json_object'}}).encode()
  req=urllib.request.Request(self.p.base_url.rstrip('/')+'/chat/completions',body,{'Content-Type':'application/json','Authorization':'Bearer '+self.p.api_key})
  try:
   with urllib.request.urlopen(req,timeout=self.p.timeout) as r: data=json.load(r)
   return data['choices'][0]['message']['content']
  except Exception as e: raise ModelError(f'{self.p.name} request failed: {e}') from e
class JevDecision:
 def __init__(self,p:Provider): self.p=p
 def choose(self,goal:str,snapshot:str)->dict:
  # Jev is a typed choice model: the endpoint may return JSON directly or OpenAI content.
  prompt=json.dumps({'task':goal,'elements':snapshot,'operations':['CLICK','TYPE_TEXT','SELECT','SCROLL','WAIT','DONE','BLOCKED']},ensure_ascii=False)
  raw=OpenAICompatible(self.p).chat('Choose exactly one browser operation and target index. Return JSON.',prompt)
  return json.loads(raw)
