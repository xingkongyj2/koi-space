"""Koi agent — Python backend that drives the app's browser view.

Speaks NDJSON HlEvents to the Electron main process and drives the browser
exclusively through `browser.py`. See `app/src/main/hl/engines/python/adapter.ts`
for the other end of the contract.
"""

__all__ = ["browser", "protocol"]
