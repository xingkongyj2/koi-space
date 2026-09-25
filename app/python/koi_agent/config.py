"""Configuration for Koi model providers (stdlib only)."""
from __future__ import annotations
import os, tomllib
from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class Provider:
    name:str; base_url:str; api_key:str; model:str; timeout:float=30
@dataclass(frozen=True)
class Settings:
    planner:Provider; decision:Provider; memory_path:str='~/.koi/memory.jsonl'; skills_path:str='~/.koi/skills.jsonl'
    max_steps:int=30; max_failures:int=3; max_seconds:float=300
def load_settings(path:str|None=None)->Settings:
    env_file=Path(os.getenv('KOI_ENV_FILE','.env')).expanduser()
    if env_file.exists():
        for raw in env_file.read_text(encoding='utf8').splitlines():
            raw=raw.strip()
            if not raw or raw.startswith('#') or '=' not in raw: continue
            key,value=raw.split('=',1); key=key.strip(); value=value.strip().strip('"\'')
            os.environ.setdefault(key,value)
    p=Path(path or os.getenv('KOI_CONFIG','~/.koi/config.toml')).expanduser(); d={}
    if p.exists():
        with p.open('rb') as f: d=tomllib.load(f)
    def provider(section, env_prefix, defaults):
        x=d.get(section,{})
        return Provider(section,str(x.get('base_url') or os.getenv(env_prefix+'_BASE_URL',defaults[0])),str(x.get('api_key') or os.getenv(env_prefix+'_API_KEY','')),str(x.get('model') or os.getenv(env_prefix+'_MODEL',defaults[1])),float(x.get('timeout') or os.getenv(env_prefix+'_TIMEOUT',30)))
    runtime=d.get('runtime',{})
    def val(section,key,env,default,cast=str):
        value=section.get(key, os.getenv(env,default))
        try:return cast(value)
        except (TypeError,ValueError):return cast(default)
    return Settings(
      provider('planner','KOI_PLANNER',('https://api.openai.com/v1','gpt-4o-mini')),
      provider('decision','KOI_DECISION',('https://api.typesafe.ai/v1','jev-latest')),
      str(d.get('memory_path',os.getenv('KOI_MEMORY_PATH','~/.koi/memory.jsonl'))),
      str(d.get('skills_path',os.getenv('KOI_SKILLS_PATH','~/.koi/skills.jsonl'))),
      val(runtime,'max_steps','KOI_MAX_STEPS',30,int), val(runtime,'max_failures','KOI_MAX_FAILURES',3,int), val(runtime,'max_seconds','KOI_MAX_SECONDS',300,float))
