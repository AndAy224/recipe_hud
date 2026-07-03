"""In-memory ring of recent log records, served by /api/system/logs.
Cross-platform (no journalctl dependency); the detached updater writes to
data/update.log instead, which the logs endpoint tails separately."""

import logging
from collections import deque


class RingHandler(logging.Handler):
    def __init__(self, maxlen: int = 400):
        super().__init__()
        self.records: deque[dict] = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            })
        except Exception:
            pass  # logging must never take the app down


ring = RingHandler()
