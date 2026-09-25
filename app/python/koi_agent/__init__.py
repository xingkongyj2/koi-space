"""Koi agent — Python backend that drives the app's browser view.

Speaks NDJSON HlEvents to the Electron main process and owns the entire browser
contract: `cdp.py` talks to Chrome DevTools Protocol directly to identify which
target is ours, `browser.py` is the only module that shells out to
`agent-browser`. See `app/src/main/hl/engines/python/adapter.ts` for the other
end of the envelope.
"""

from . import browser, cdp, protocol

__all__ = ["browser", "cdp", "protocol"]
