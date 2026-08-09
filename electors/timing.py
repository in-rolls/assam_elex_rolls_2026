"""Logging and wall-clock accounting for a run.

A constituency is a multi-hour job, and the first version of it printed a line per part with no
timestamp and no duration. That is enough to see it is alive and nothing else: not how long a
part takes, not whether it is speeding up as the cache fills, not what it will cost to do the
remaining 125 constituencies. When a run was abandoned at an estimated 36 hours, the estimate
came from watching the terminal, which is the sort of number that turns out to have been wrong.

So each part is timed **inside its worker**. A process pool yields results in submission order,
so the wall-clock gap between two yields is whatever the slowest earlier part was still doing,
not the duration of the part that just arrived.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import List, Optional

LOG_DIR = Path("out/logs")

#: Timestamped, because the question a long run has to answer afterwards is *when* something
#: happened, not just that it did.
FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
DATEFMT = "%H:%M:%S"


def setup(name: str, to_file: bool = True, level: int = logging.INFO) -> logging.Logger:
    """A logger that writes to the console and, for runs worth keeping, to a dated file."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, DATEFMT))
    logger.addHandler(console)

    if to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        handler = logging.FileHandler(LOG_DIR / f"{name}-{stamp}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s " + FORMAT[10:], None))
        logger.addHandler(handler)
    return logger


def human(seconds: float) -> str:
    """``2h 14m`` -- the unit a multi-hour job is actually discussed in."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


@dataclass
class RunClock:
    """Wall time for a whole run, and the per-part durations that make it up.

    Cached parts are counted separately. Mixing them into the mean makes a resumed run look
    faster than the pipeline is, which is exactly the number nobody should be quoting.
    """

    total: int
    started: float = field(default_factory=time.time)
    worked: List[float] = field(default_factory=list)
    cached: int = 0

    @property
    def done(self) -> int:
        return len(self.worked) + self.cached

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    def record(self, seconds: Optional[float], was_cached: bool) -> None:
        if was_cached:
            self.cached += 1
        elif seconds is not None:
            self.worked.append(seconds)

    @property
    def eta(self) -> Optional[float]:
        """Remaining wall time, from throughput actually achieved rather than per-part cost.

        Elapsed over parts done already accounts for however many workers are running and
        whatever else the machine is doing, both of which a per-part average ignores.
        """
        if not self.done or self.done >= self.total:
            return None
        return self.elapsed / self.done * (self.total - self.done)

    def progress(self, part: int, electors: int, seconds: Optional[float], was_cached: bool) -> str:
        note = " (cached)" if was_cached else f" in {human(seconds or 0.0)}"
        eta = self.eta
        tail = f", eta {human(eta)}" if eta is not None else ""
        return (
            f"{self.done:>3}/{self.total} part {part:<4} {electors:>4} electors{note}"
            f" | elapsed {human(self.elapsed)}{tail}"
        )

    def summary(self) -> List[str]:
        """What the run cost, in the terms the next one has to be planned in."""
        lines = [
            f"wall clock       {human(self.elapsed)} for {self.done} parts "
            f"({self.cached} served from cache)",
        ]
        if self.worked:
            lines += [
                f"per part         mean {human(sum(self.worked) / len(self.worked))}, "
                f"median {human(median(self.worked))}, "
                f"slowest {human(max(self.worked))}, fastest {human(min(self.worked))}",
                f"throughput       {self.done / (self.elapsed / 3600):.1f} parts/hour "
                f"across all workers",
            ]
        return lines
