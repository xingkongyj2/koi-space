"""Code-first verification for actions and planner completion criteria."""
from __future__ import annotations

import re

from . import protocol


class Validator:
    def action(self, before, after, action) -> bool:
        changed = before.url != after.url or before.snapshot != after.snapshot
        passed = action.kind in {"wait", "scroll"} or changed
        protocol.log(
            f"flow=validate action={action.kind} "
            f"status={'passed' if passed else 'noop'}"
        )
        return passed

    def step(self, observation, criteria, start_url="") -> bool:
        text = f"{observation.url}\n{observation.snapshot}".lower()
        if start_url and not observation.url.startswith(start_url.rstrip("/")):
            return False
        for criterion in criteria:
            criterion = criterion.strip()
            if not criterion or criterion.startswith("页面"):
                continue
            lower = criterion.lower()
            if lower.startswith("url contains:"):
                expected = criterion[13:].strip().lower()
                if expected not in observation.url.lower():
                    return False
            elif lower.startswith("count >="):
                expected = int(criterion.split(">=", 1)[1].strip())
                if len(observation.elements) < expected:
                    return False
            elif lower not in text and not re.search(criterion, text, re.I):
                return False
        return True
