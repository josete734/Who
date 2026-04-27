"""Long-running Certificate Transparency watcher.

Polls the certspotter issuances API every few minutes for each row in the
``ct_watchlist`` table, persists new certificates as findings and emits
``ct.new_cert`` events on the per-case event bus.

Wiring into Arq is intentionally left to the integration agent — see
``app.ct_watcher.runner`` for the WIRING comment block.
"""
from app.ct_watcher.runner import poll_domain, run_watcher_tick  # noqa: F401
