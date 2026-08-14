"""The fleet view, and the two things it exists to catch.

A status command that only agrees with itself proves nothing, so these tests construct states
that the bucket cannot conveniently be put into: a machine that died holding work, and a fleet
that changed size mid-run.
"""

from datetime import datetime, timedelta, timezone

from electors import fleet


def _at(hours_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def test_a_dead_machine_is_not_progress():
    """A claim nobody is refreshing must read STALE, not 'in flight'.

    This is the failure that hides: the constituency looks worked-on, no other machine may take
    it until the claim expires, and the queue silently loses a slot.
    """
    alive = fleet.Constituency(ac_no=1, held_by="box-a", beat_age=60)
    dead = fleet.Constituency(ac_no=2, held_by="box-b", beat_age=fleet.STALE + 1)
    assert alive.state == "in flight"
    assert dead.state == "STALE"


def test_finished_beats_a_lingering_claim():
    """The shard is written last, so its presence outranks any claim still sitting in the bucket."""
    entry = fleet.Constituency(ac_no=3, held_by="box-a", beat_age=99999, finished=_at(1))
    assert entry.state == "done"


def test_states_partition_every_constituency():
    """Nothing may be counted twice or fall through -- 'queued' is derived by exclusion."""
    everything = [
        fleet.Constituency(ac_no=1, finished=_at(2)),
        fleet.Constituency(ac_no=2, held_by="box-a", beat_age=10),
        fleet.Constituency(ac_no=3, held_by="box-b", beat_age=fleet.STALE + 5),
        fleet.Constituency(ac_no=4, uploaded=_at(3)),
    ]
    state = fleet.Fleet(constituencies=everything)
    counted = sum(len(state.of(s)) for s in ("done", "in flight", "STALE", "queued"))
    assert counted == len(everything)


def test_throughput_survives_the_fleet_growing():
    """Rate must come from per-constituency duration, not from gaps between shards.

    The run really did grow from one machine to four. Gap-based arithmetic read that as 0.6
    constituencies an hour when the four machines were doing three times that, and a status
    command that understates the fleet argues for buying machines already bought.

    Here: two constituencies each took 2 hours, and four machines hold work. Truth is 4/2 = 2.0
    an hour. The shards landed 6 hours apart because the early one ran alone, so the gap estimate
    would say about 0.17 -- an order of magnitude wrong.
    """
    state = fleet.Fleet(
        constituencies=[
            fleet.Constituency(ac_no=1, started=_at(8), finished=_at(6)),
            fleet.Constituency(ac_no=2, started=_at(2), finished=_at(0.001)),
            fleet.Constituency(ac_no=3, held_by="box-a", beat_age=10),
            fleet.Constituency(ac_no=4, held_by="box-b", beat_age=10),
            fleet.Constituency(ac_no=5, held_by="box-c", beat_age=10),
            fleet.Constituency(ac_no=6, held_by="box-d", beat_age=10),
        ]
    )
    assert state.machines == 4
    assert abs(state.hours_each - 2.0) < 0.01
    _, finishing = state.rates
    assert abs(finishing - 2.0) < 0.05


def test_rate_falls_back_when_nothing_has_been_timed():
    """Claims written before start times were recorded still yield a usable, if lagging, rate."""
    state = fleet.Fleet(
        constituencies=[
            fleet.Constituency(ac_no=1, finished=_at(4)),
            fleet.Constituency(ac_no=2, finished=_at(2)),
            fleet.Constituency(ac_no=3, held_by="box-a", beat_age=10),
        ]
    )
    assert state.hours_each is None
    _, finishing = state.rates
    assert abs(finishing - 0.5) < 0.01


def test_drain_time_answers_how_long_the_queue_takes():
    state = fleet.Fleet(
        constituencies=[
            fleet.Constituency(ac_no=1, started=_at(2), finished=_at(0.001)),
            fleet.Constituency(ac_no=2, held_by="box-a", beat_age=10),
            fleet.Constituency(ac_no=3, held_by="box-b", beat_age=10),
        ]
        + [fleet.Constituency(ac_no=n, uploaded=_at(1)) for n in range(10, 20)]
    )
    # Two machines, 2 hours each, is one an hour; ten queued is about ten hours.
    assert abs(state.drain_hours - 10.0) < 0.5


def test_render_names_the_stale_machine():
    """The warning has to appear in the output, not merely in the data."""
    state = fleet.Fleet(
        constituencies=[
            fleet.Constituency(
                ac_no=7, held_by="box-z", beat_age=fleet.STALE + 60, parts_done=5, parts_total=10
            )
        ]
    )
    text = fleet.render(state)
    assert "STALE" in text
    assert "box-z" in text


def test_stale_matches_the_workers():
    """The reader's threshold must be the writers'.

    ``fleet`` calls a claim abandoned at one number and the workers steal it at another; if those
    drift, the status command describes a fleet that does not exist -- reporting work as stranded
    that nobody will take, or as healthy when it has already been stolen. Both were changed by
    hand together once, which is exactly when they will not be next time.
    """
    import re
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "gcp_worker.sh"
    match = re.search(r"^STALE=(\d+)", script.read_text(), re.MULTILINE)
    assert match, "gcp_worker.sh no longer defines STALE"
    assert int(match.group(1)) == fleet.STALE


def test_on_disk_looks_where_the_browser_saves(tmp_path):
    """An archive in the downloads folder counts as on disk, not as still to download.

    This is the case that failed. Nine complete archives, 29 GB, sat in ~/Downloads while the
    status command reported those constituencies as missing, and eight were downloaded a second
    time on the strength of it. Searching one of the two places a file can be is confidently
    wrong, which is worse than silent.
    """
    staging, downloads = tmp_path / "staging", tmp_path / "downloads"
    staging.mkdir()
    downloads.mkdir()
    (downloads / "AC77_ASM.zip").write_bytes(b"x")
    (staging / "AC12_BEN.zip").write_bytes(b"x")

    assert fleet.on_disk((staging, downloads))[0] == [12, 77]
    assert fleet.on_disk((staging,))[0] == [12]


def test_on_disk_still_reports_nothing_when_there_is_nothing(tmp_path):
    """The check must be able to fire -- one that never returns empty proves nothing."""
    assert fleet.on_disk((tmp_path,)) == ([], 0)


def test_on_disk_counts_partial_downloads_across_both(tmp_path):
    """Partials cannot be named -- a browser hides the AC number until a download finishes -- so
    they are counted, and counted wherever they are."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "Unconfirmed 111.crdownload").write_bytes(b"x")
    (b / "Unconfirmed 222.crdownload").write_bytes(b"x")
    assert fleet.on_disk((a, b))[1] == 2
