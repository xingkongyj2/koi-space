"""Small persistent skill library for deterministic repeated browser tasks."""
from __future__ import annotations
import json, re
from pathlib import Path
from .executor import Action

class SkillLibrary:
    def __init__(self, path=None): self.path=Path(path).expanduser() if path else None; self._items=[]; self._load()
    def _load(self):
        if not self.path or not self.path.exists(): return
        for line in self.path.read_text(encoding='utf8').splitlines():
            try: self._items.append(json.loads(line))
            except ValueError: pass
    def match(self, goal, url=''):
        terms=set(re.findall(r'\w+', goal.lower())); best=None
        for item in self._items:
            score=len(terms & set(item.get('terms',())))
            if url and item.get('host') and item['host'] not in url: score-=2
            if score and (best is None or score>best[0]): best=(score,item)
        return best[1] if best else None
    def save(self, goal, actions, url=''):
        if not self.path:return
        item={'terms':list(set(re.findall(r'\w+',goal.lower()))),'host':url.split('/')[2] if '//' in url else '', 'actions':[a.__dict__ for a in actions]}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
    @staticmethod
    def actions(item): return tuple(Action(**a) for a in item.get('actions',()))
