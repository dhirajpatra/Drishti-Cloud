from __future__ import annotations

import logging
import os
import sys

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        logging.basicConfig(
            stream=sys.stdout,
            level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        )
        _configured = True
    return logging.getLogger(name)
