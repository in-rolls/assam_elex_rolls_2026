"""Where the run is, in one screen.

"Is everything I uploaded being worked on?" was asked four times and answered four times by
hand, with eight ``gcloud`` invocations that left nothing behind. The answers were right and the
method was not: an ad-hoc query proves something once, to one person, and cannot be re-run when
the numbers change an hour later.

The state lives entirely in the bucket, and the pieces already exist -- the worker writes claims
(``gcp_worker.sh``), the shard is written last so its presence means finished, and the archives
are the record of what was uploaded. This just reads them together.

**Queued is the number that matters.** A constituency sitting in ``source/`` with no shard and no
live claim is uploaded, paid for in bandwidth, and idle. Nothing was alerting on that, and it had
reached nineteen.

**A stale claim is worse than no claim**, because it looks like progress. A machine that dies
holding one leaves the constituency untouchable by the others until ``STALE`` expires, and until
now nothing reported it.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Matches ``gcp_worker.sh``'s ``STALE``. A claim older than this is one the workers themselves
#: will steal, so reporting a different threshold here would describe a fleet that does not exist.
STALE = 10800

#: ``gcloud storage ls -l`` prints ``size  timestamp  url``. Parsed rather than asked for as JSON
#: because the JSON listing of 29 archives is megabytes of metadata to extract two fields from.
LISTING = re.compile(r"^\s*(\d+)\s+(\S+)\s+(gs://\S+)$")


@dataclass
class Constituency:
    """One constituency's position in the run."""

    ac_no: int
    archive: str = ""
    uploaded: Optional[datetime] = None
    finished: Optional[datetime] = None
    held_by: str = ""
    beat_age: int = 0
    started: Optional[datetime] = None
    parts_done: int = 0
    parts_total: int = 0

    @property
    def hours(self) -> Optional[float]:
        """How long this constituency took, or has taken so far.

        Measured from the claim rather than from the gap to the previous shard. The gap is what a
        fleet of one machine and a fleet of four have in common only by accident, and reading one
        as the other is how throughput came out four times too low.
        """
        if not self.started:
            return None
        end = self.finished or datetime.now(timezone.utc)
        return (end - self.started).total_seconds() / 3600

    @property
    def state(self) -> str:
        if self.finished:
            return "done"
        if self.held_by and self.beat_age <= STALE:
            return "in flight"
        if self.held_by:
            return "STALE"
        return "queued"

    @property
    def progress(self) -> str:
        if not self.parts_total:
            return ""
        return (
            f"{self.parts_done}/{self.parts_total} parts ({self.parts_done/self.parts_total:.0%})"
        )


@dataclass
class Fleet:
    """Everything the bucket knows about the run."""

    constituencies: List[Constituency] = field(default_factory=list)
    #: Constituency to the archives holding it, when there is more than one. Nothing downstream
    #: can be corrupted by this -- the shard path, the claim and the discovery loop all key on the
    #: AC number, so a second archive is processed by nobody and overwrites nothing. It is
    #: reported because it means 3 GB is being stored twice and, worse, that which of two editions
    #: was actually read is decided silently by ``sort -V``.
    duplicates: Dict[int, List[str]] = field(default_factory=dict)
    #: Constituencies in the published index with no archive in the bucket -- what is left to
    #: download. The bucket is the authoritative list of what is in play; this is its complement,
    #: and it exists so that "what next" never has to be answered from memory.
    not_uploaded: List[int] = field(default_factory=list)

    def of(self, state: str) -> List[Constituency]:
        return [c for c in self.constituencies if c.state == state]

    @property
    def machines(self) -> int:
        """Distinct hosts currently holding work -- the fleet as it is, not as it was."""
        return len({c.held_by for c in self.of("in flight") if c.held_by})

    @property
    def hours_each(self) -> Optional[float]:
        """Typical machine-hours for one constituency, from claim to shard.

        This is the physical constant of the run, and unlike a completion rate it does not change
        when machines are added. Throughput is then ``machines / hours_each``, which is what makes
        "add four more" a prediction rather than a hope.
        """
        done = sorted(c.hours for c in self.of("done") if c.hours is not None)
        if not done:
            return None
        return done[len(done) // 2]

    @property
    def rates(self) -> Tuple[float, float]:
        """Archives arriving per hour, and constituencies finishing per hour.

        The comparison is the whole point: a fleet slower than the uploads has a queue that grows
        without bound, and no snapshot of "19 waiting" says whether that is a backlog clearing or
        a backlog forming.

        Finishing is ``machines / hours_each`` once any constituency has been timed from its
        claim. Falling back to the gaps between shards reads a fleet that grew from one machine to
        four as one running at a quarter of its true speed -- which is exactly what it did read,
        and what would have argued for buying machines that were already bought.
        """
        arriving = _per_hour([c.uploaded for c in self.constituencies if c.uploaded])
        each = self.hours_each
        if each and each > 0 and self.machines:
            return arriving, self.machines / each
        return arriving, _per_hour([c.finished for c in self.constituencies if c.finished])

    @property
    def drain_hours(self) -> Optional[float]:
        """How long the queue takes at the current rate, or None if nothing is finishing."""
        _, finishing = self.rates
        return len(self.of("queued")) / finishing if finishing > 0 else None


#: How many recent events a rate is measured over. Small enough to track a fleet that changed
#: size an hour ago, large enough that one slow constituency does not dominate.
WINDOW = 4


def _per_hour(stamps: List[datetime], window: int = WINDOW) -> float:
    recent = sorted(stamps)[-(window + 1) :]
    if len(recent) < 2:
        return 0.0
    span = (recent[-1] - recent[0]).total_seconds() / 3600
    return (len(recent) - 1) / span if span > 0 else 0.0


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def _listing(pattern: str) -> Dict[str, datetime]:
    """Object name to upload time, for one glob."""
    out: Dict[str, datetime] = {}
    for line in _run("gcloud", "storage", "ls", "-l", pattern).splitlines():
        match = LISTING.match(line)
        if match:
            when = datetime.strptime(match.group(2), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            out[match.group(3).rsplit("/", 1)[-1]] = when
    return out


def published_parts(info: Path = Path("dataset/parts.jsonl.gz")) -> Dict[int, int]:
    """How many parts each constituency publishes, so progress is a fraction and not a count."""
    counts: collections.Counter = collections.Counter()
    if not info.exists():
        return {}
    with gzip.open(info, "rt", encoding="utf-8") as handle:
        for line in handle:
            ac_no = json.loads(line).get("ac_no")
            if ac_no is not None:
                counts[int(ac_no)] += 1
    return dict(counts)


def survey(bucket: str, info: Path = Path("dataset/parts.jsonl.gz")) -> Fleet:
    """Read the whole run's state from the bucket."""
    archives = _listing(f"{bucket}/source/AC*.zip")
    shards = _listing(f"{bucket}/shards/AC*.parquet")
    totals = published_parts(info)

    found: Dict[int, Constituency] = {}
    holding: Dict[int, List[str]] = collections.defaultdict(list)
    for name, when in sorted(archives.items()):
        ac_no = int(re.sub(r"^AC0*(\d+)_.*", r"\1", name))
        holding[ac_no].append(name)
        found[ac_no] = Constituency(
            ac_no=ac_no, archive=name, uploaded=when, parts_total=totals.get(ac_no, 0)
        )
    for name, when in shards.items():
        ac_no = int(name[2:5])
        found.setdefault(ac_no, Constituency(ac_no=ac_no, parts_total=totals.get(ac_no, 0)))
        found[ac_no].finished = when

    now = int(datetime.now(timezone.utc).timestamp())
    for line in _run("gcloud", "storage", "ls", f"{bucket}/claims/").splitlines():
        name = line.strip().rsplit("/", 1)[-1]
        if not name.endswith(".claim"):
            continue
        ac_no = int(re.sub(r"^AC0*(\d+)\.claim$", r"\1", name))
        held = _run("gcloud", "storage", "cat", f"{bucket}/claims/{name}").split()
        if len(held) < 2:
            continue
        entry = found.setdefault(ac_no, Constituency(ac_no=ac_no, parts_total=totals.get(ac_no, 0)))
        # "host beat" is what workers wrote before start times were recorded; those claims can
        # still say who holds the work and whether it is alive, just not how long it has taken.
        entry.held_by, entry.beat_age = held[0], now - int(held[-1])
        if len(held) >= 3:
            entry.started = datetime.fromtimestamp(int(held[1]), tz=timezone.utc)

    # Only for what is running. Listing every constituency's stage1 tree is one round trip each
    # for information that is already settled everywhere else.
    for entry in found.values():
        if entry.state in ("in flight", "STALE"):
            listed = _run("gcloud", "storage", "ls", f"{bucket}/stage1/AC{entry.ac_no:03d}/")
            entry.parts_done = sum(1 for line in listed.splitlines() if "/part" in line)

    return Fleet(
        constituencies=[found[k] for k in sorted(found)],
        duplicates={k: v for k, v in sorted(holding.items()) if len(v) > 1},
        not_uploaded=sorted(set(totals) - set(found)),
    )


def on_disk(rolls: Path = Path("data/ac_rolls")) -> Tuple[List[int], int]:
    """Constituencies downloaded but not yet uploaded, and how many downloads are still running.

    Both belong in "what is left to fetch", and neither is in the bucket. The finished ones can be
    named. The running ones cannot: a browser calls a partial download ``Unconfirmed
    72972.crdownload`` and only restores the real filename when it completes, so nineteen
    constituencies in flight are nineteen unknowns. Counting them is the most that can honestly be
    said, and saying it is what stops the remaining list from being read as untouched.
    """
    if not rolls.exists():
        return [], 0
    finished = sorted({int(re.sub(r"^AC0*(\d+)_.*", r"\1", p.name)) for p in rolls.glob("AC*.zip")})
    return finished, len(list(rolls.glob("*.crdownload")))


def as_ranges(numbers: Sequence[int]) -> str:
    """``1 2 3 7 9 10`` as ``1-3, 7, 9-10``. Ninety-seven constituency numbers is a wall."""
    spans: List[str] = []
    for number in sorted(numbers):
        if spans and number == _end(spans[-1]) + 1:
            spans[-1] = f"{_start(spans[-1])}-{number}"
        else:
            spans.append(str(number))
    return ", ".join(spans)


def _start(span: str) -> int:
    return int(span.split("-")[0])


def _end(span: str) -> int:
    return int(span.split("-")[-1])


def render(fleet: Fleet, free_gb: Optional[float] = None) -> str:
    """The run, as something you can read in one look."""
    lines: List[str] = []
    arriving, finishing = fleet.rates

    for state in ("done", "in flight", "STALE", "queued"):
        group = fleet.of(state)
        if not group:
            continue
        numbers = ", ".join(str(c.ac_no) for c in group)
        lines.append(f"   {state:<10} {len(group):>3}   {numbers}")
        for entry in group:
            if state in ("in flight", "STALE") and entry.progress:
                held = f"{entry.held_by}, beat {entry.beat_age//60} min ago"
                elapsed = f", {entry.hours:.1f} h in" if entry.hours is not None else ""
                lines.append(
                    f"                    AC{entry.ac_no}  {entry.progress}  {held}{elapsed}"
                )

    each = fleet.hours_each
    lines.append("")
    lines.append(f"   archives arriving   {arriving:.1f}/hour   (last {WINDOW} uploads)")
    if each:
        lines.append(
            f"   shards finishing    {finishing:.1f}/hour   "
            f"({fleet.machines} machines x {each:.1f} h each)"
        )
    else:
        lines.append(f"   shards finishing    {finishing:.1f}/hour   (last {WINDOW} completions)")
        lines.append(
            "   ! no constituency has been timed from its claim yet, so that rate lags a fleet"
        )
        lines.append("     that changed size -- treat the drain estimate below as a lower bound")

    hours = fleet.drain_hours
    if hours is None:
        lines.append("   ! nothing is finishing -- the queue cannot drain")
    elif hours > 0:
        lines.append(f"   the {len(fleet.of('queued'))} queued drain in about {hours:.0f} h")
    drift = arriving - finishing
    if drift > 0.05:
        lines.append(
            f"   ! if you keep uploading at {arriving:.1f}/hour the queue grows by "
            f"{drift:.1f}/hour -- the fleet is too small to keep up"
        )

    stale = fleet.of("STALE")
    if stale:
        lines.append("")
        lines.append(
            f"   ! {len(stale)} constituencies held by a machine that stopped reporting; "
            f"another worker may take them after {STALE//3600} h"
        )
    if fleet.duplicates:
        lines.append("")
        for ac_no, names in fleet.duplicates.items():
            lines.append(f"   ! AC{ac_no} has {len(names)} archives: {', '.join(names)}")
        lines.append("     no rows can be duplicated -- shard, claim and discovery all key on the")
        lines.append("     AC number -- but only one is ever read, and which one is arbitrary")

    if fleet.not_uploaded:
        waiting, running = on_disk()
        left = [n for n in fleet.not_uploaded if n not in set(waiting)]
        lines.append("")
        if waiting:
            lines.append(f"   downloaded, not yet uploaded   {len(waiting)}")
            lines.append(f"     {as_ranges(waiting)}")
        lines.append(f"   not anywhere yet   {len(left)}")
        lines.append(f"     {as_ranges(left)}")
        if running:
            lines.append(
                f"     up to {running} of those are downloading now -- a browser hides the AC"
            )
            lines.append(
                f"     number until a download finishes, so about {len(left) - running} are"
                " genuinely untouched"
            )

    if free_gb is not None:
        lines.append("")
        lines.append(f"   laptop free space   {free_gb:.0f} GB")
    return "\n".join(lines)
