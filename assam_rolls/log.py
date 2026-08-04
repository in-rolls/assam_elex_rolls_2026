"""Logging setup for pipeline runs.

A statewide run is ~28,000 pages over several hours across many worker processes. When
one page fails, the useful question afterwards is *which* page and *why* -- so every
failure is logged with its part key (``{ac:03d}-{part:04d}``), making the log greppable
by the same identifier that names the cache entry and the page image.

Console output stays terse (the CLI prints its own human summary); the file handler keeps
the full record. Worker processes log through the same configuration, so a failure inside
a pool still lands in the file.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOGGER_NAME = "assam_rolls"

#: Keep several runs of history without unbounded growth.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

CONSOLE_FORMAT = "%(levelname)-7s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(process)d %(name)s: %(message)s"


def default_log_path(out_dir: Path) -> Path:
    """A timestamped log file under ``out_dir/logs``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return out_dir / "logs" / f"run-{stamp}.log"


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    console: bool = True,
) -> logging.Logger:
    """Configure the package logger. Safe to call more than once.

    The file handler is always DEBUG regardless of console level: when something goes
    wrong mid-run, the detail is wanted in the file even though it was too noisy to show.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    if console:
        stream = logging.StreamHandler()
        stream.setLevel(level)
        stream.setFormatter(logging.Formatter(CONSOLE_FORMAT))
        logger.addHandler(stream)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        rotating.setLevel(logging.DEBUG)
        rotating.setFormatter(logging.Formatter(FILE_FORMAT))
        logger.addHandler(rotating)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Logger for a module, as a child of the package logger."""
    suffix = name.split(".")[-1]
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}")


class RunSummary:
    """Counts and timings for one run, emitted together at the end.

    Progress printed per page scrolls away; what is worth keeping is the tally. Failures
    are held with their part keys so the summary names them rather than only counting.
    """

    def __init__(self, logger: logging.Logger, what: str) -> None:
        self.logger = logger
        self.what = what
        self.started = datetime.now(timezone.utc)
        self.ok = 0
        self.skipped = 0
        self.failures: list[tuple[str, str]] = []

    def succeeded(self, key: str) -> None:
        self.ok += 1
        self.logger.debug("%s: ok", key)

    def was_cached(self, key: str) -> None:
        self.skipped += 1
        self.logger.debug("%s: cached, skipped", key)

    def failed(self, key: str, reason: str) -> None:
        self.failures.append((key, reason))
        self.logger.warning("%s: %s", key, reason)

    @property
    def elapsed_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.started).total_seconds()

    def emit(self) -> dict:
        total = self.ok + self.skipped + len(self.failures)
        per_item = self.elapsed_seconds / total if total else 0.0
        self.logger.info(
            "%s: %d done, %d cached, %d failed in %.0fs (%.2fs each)",
            self.what,
            self.ok,
            self.skipped,
            len(self.failures),
            self.elapsed_seconds,
            per_item,
        )
        for key, reason in self.failures[:20]:
            self.logger.info("  failed %s: %s", key, reason)
        if len(self.failures) > 20:
            self.logger.info("  ... and %d more failures", len(self.failures) - 20)
        return {
            "processed": self.ok,
            "cached": self.skipped,
            "failed": len(self.failures),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "seconds_per_item": round(per_item, 3),
            "failures": [{"key": k, "reason": r} for k, r in self.failures],
        }


def worker_init(log_file: Optional[str], level: int) -> None:
    """Re-establish logging inside a pool worker.

    ``multiprocessing`` on macOS spawns rather than forks, so a worker starts with no
    handlers and its warnings would otherwise vanish.
    """
    setup_logging(
        level=level,
        log_file=Path(log_file) if log_file else None,
        console=False,
    )
    get_logger(__name__).debug("worker %d ready", os.getpid())
