from __future__ import annotations

import logging
import sys


SENSITIVE_WORDS = ("token", "secret", "authorization", "credential", "refresh")


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        if any(word in message for word in SENSITIVE_WORDS):
            # Keep the log entry, but make accidental secret logging obvious.
            record.msg = "[redacted sensitive log message]"
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger().addFilter(SecretFilter())
