"""What this run has cost so far, counted from what it actually consumed.

Cloud Billing does not expose live spend to the CLI -- that needs a BigQuery export, which takes
a day to start reporting -- and a budget alert only fires after the money is gone. So this counts
the three things that are billed, from sources that cannot drift from reality:

- **Vision images**, from the shard manifest each constituency writes. That is the same number
  the invoice is computed from, because it is a count of requests actually made.
- **VM hours**, from Compute Engine's own operation log: every insert, start, stop and delete.
- **Bytes stored**, from the bucket listing, prorated by the hour rather than charged as a month.

Rates are listed here rather than looked up, so a price change is a visible edit rather than a
silent drift in what gets quoted. us-central1, August 2026.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

#: n2d-standard-32 on demand: 32 vCPU at $0.028 and 128 GB at $0.0037 an hour.
VM_HOUR = 1.37
#: 200 GB of pd-balanced at $0.10 a GB-month.
DISK_HOUR = 200 * 0.10 / 730
#: Cloud Vision DOCUMENT_TEXT_DETECTION, 1,001 to 5,000,000 units a month.
PER_IMAGE = 1.50 / 1000
#: Cloud Storage Standard, us-central1.
STORAGE_GB_MONTH = 0.020
#: Egress to the internet, first 10 TB.
EGRESS_GB = 0.12


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def vm_hours(zone: str = "us-central1-a") -> tuple[float, list[str]]:
    """Billed instance time, from Compute Engine's own record of starts and stops.

    Taken from the operation log rather than from uptime, because an instance that was
    preempted, deleted and recreated has several lives and only the log remembers them all.
    """
    raw = run(
        "gcloud",
        "compute",
        "operations",
        "list",
        "--filter",
        "targetLink~assam-rolls",
        "--format",
        "value(operationType,targetLink,insertTime)",
    )
    events: dict[str, list[tuple[datetime, str]]] = {}
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        kind, target, when = parts[0], parts[1].rsplit("/", 1)[-1], parts[2]
        stamp = datetime.fromisoformat(when.replace("Z", "+00:00"))
        events.setdefault(target, []).append((stamp, kind))

    total = 0.0
    notes = []
    now = datetime.now(timezone.utc)
    for name, log in events.items():
        started = None
        alive = 0.0
        for stamp, kind in sorted(log):
            if kind in ("insert", "start"):
                started = stamp
            elif kind in ("stop", "delete", "compute.instances.preempted") and started:
                alive += (stamp - started).total_seconds() / 3600
                started = None
        if started:
            alive += (now - started).total_seconds() / 3600
            notes.append(f"{name} still running")
        total += alive
        notes.append(f"{name}: {alive:.2f} h")
    return total, notes


def main() -> int:
    manifest = Path(sys.argv[1] if len(sys.argv) > 1 else "out/manifest.json")
    images = rows = 0
    acs = 0
    if manifest.exists():
        for entry in json.loads(manifest.read_text(encoding="utf-8")).values():
            images += entry.get("images_billed") or 0
            rows += entry.get("rows") or 0
            acs += 1

    hours, notes = vm_hours()
    stored = run("gcloud", "storage", "du", "-s", "gs://sawasdee-assam-rolls").split()
    gb = int(stored[0]) / 1e9 if stored and stored[0].isdigit() else 0.0

    vision = images * PER_IMAGE
    compute = hours * (VM_HOUR + DISK_HOUR)
    # Charged by the hour, so a few days of a big bucket is not a month of one.
    storage = gb * STORAGE_GB_MONTH * (hours / 730) if hours else 0.0

    print(f"{acs} constituencies, {rows:,} rows\n")
    print(f"   Cloud Vision   {images:>8,} images        ${vision:>8.2f}")
    print(f"   Compute        {hours:>8.2f} instance-h    ${compute:>8.2f}")
    print(f"   Storage        {gb:>8.1f} GB            ${storage:>8.2f}")
    print(f"   {'':<14} {'':>8}               {'-' * 9}")
    print(f"   so far{'':<24}      ${vision + compute + storage:>8.2f}")
    for note in notes:
        print(f"      {note}")

    if rows:
        per_ac = (vision + compute + storage) / max(acs, 1)
        print(f"\n   per constituency so far          ${per_ac:>8.2f}")
        print(f"   implied for 126                  ${per_ac * 126:>8.2f}")
        print(f"   (projection was ~$185 on spot, ~$285 on demand)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
