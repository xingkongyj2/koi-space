"""Deterministic checks for browser actions and planner criteria."""
from __future__ import annotations

from . import protocol


class Validator:
    @protocol.traced("validator.action")
    def action(self, before, after, action) -> bool:
        changed = (before.url != after.url or before.snapshot != after.snapshot
                   or before.page_text != after.page_text)
        passed = action.kind in {"wait", "scroll"} or changed
        protocol.log(
            f"flow=validate action={action.kind} "
            f"status={'passed' if passed else 'unchanged'}"
        )
        return passed

    @protocol.traced("validator.step")
    def step(self, observation, criteria, start_url="") -> bool:
        """Check planned conditions without treating the entry URL as a final URL.

        A separate completion verifier can resolve inaccurate planned criteria
        against the actual page and action evidence.
        """
        text = f"{observation.url}\n{observation.snapshot}\n{observation.page_text}".lower()
        for criterion in criteria:
            if isinstance(criterion, dict):
                kind = str(criterion.get("type", "")).strip().lower()
                value = criterion.get("value")
                if kind == "goal_state":
                    # This criterion intentionally requires semantic/model
                    # verification; deterministic page checks cannot prove it.
                    return False
                if kind == "url_prefix":
                    expected = str(value).strip().rstrip("/").lower()
                    if not observation.url.lower().startswith(expected):
                        return False
                elif kind == "url_contains":
                    if str(value).lower() not in observation.url.lower():
                        return False
                elif kind == "text_contains":
                    if str(value).lower() not in text:
                        return False
                elif kind == "element_text":
                    expected = str(value).lower()
                    if not any(expected in str(element.get("text", "")).lower()
                               for element in observation.elements):
                        return False
                elif kind == "element_count_at_least":
                    if len(observation.elements) < int(value):
                        return False
                else:
                    return False
                continue

            criterion = str(criterion).strip()
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
                if expected not in text:
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
