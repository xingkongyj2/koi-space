"""Code-first verification for actions and planner completion criteria."""
from __future__ import annotations

import re

from . import protocol


class Validator:
    @protocol.traced("validator.action")
    def action(self, before, after, action) -> bool:
        changed = before.url != after.url or before.snapshot != after.snapshot
        passed = action.kind in {"wait", "scroll"} or changed
        protocol.log(
            f"flow=validate action={action.kind} "
            f"status={'passed' if passed else 'noop'}"
        )
        return passed

    @protocol.traced("validator.step")
    def step(self, observation, criteria, start_url="") -> bool:
        text = f"{observation.url}\n{observation.snapshot}".lower()
        if start_url and not observation.url.startswith(start_url.rstrip("/")):
            return False
        for criterion in criteria:
            criterion = criterion.strip()
            if not criterion:
                return False
            lower = criterion.lower()
            if lower.startswith("url_prefix:"):
                expected = criterion.split(":", 1)[1].strip().rstrip("/").lower()
                if not observation.url.lower().startswith(expected):
                    return False
            elif lower.startswith(("url_contains:", "url contains:")):
                expected = criterion.split(":", 1)[1].strip().lower()
                if expected not in observation.url.lower():
                    return False
            elif lower.startswith("text_contains:"):
                expected = criterion.split(":", 1)[1].strip().lower()
                if expected not in observation.snapshot.lower():
                    return False
            elif lower.startswith("count >="):
                try:
                    expected = int(criterion.split(">=", 1)[1].strip())
                except ValueError:
                    return False
                if len(observation.elements) < expected:
                    return False
            elif lower not in text:
                return False
        return True
