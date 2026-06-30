"""로깅 설정 — 콘솔 + logs/YYYY-MM-DD.log 파일 저장. (NFR-06)"""
from __future__ import annotations

import logging
import os
from datetime import datetime

_LOG_DIR = "logs"
_CONFIGURED = False


def get_logger(name: str = "tracker") -> logging.Logger:
    """일자별 로그 파일과 콘솔에 동시에 출력하는 로거를 반환한다."""
    global _CONFIGURED
    logger = logging.getLogger(name)

    if not _CONFIGURED:
        os.makedirs(_LOG_DIR, exist_ok=True)
        log_path = os.path.join(_LOG_DIR, f"{datetime.now():%Y-%m-%d}.log")

        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        )

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)

        root = logging.getLogger("tracker")
        root.setLevel(logging.INFO)
        root.addHandler(file_handler)
        root.addHandler(console_handler)
        root.propagate = False
        _CONFIGURED = True

    if not name.startswith("tracker"):
        name = f"tracker.{name}"
    return logging.getLogger(name)
