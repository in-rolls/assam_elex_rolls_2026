"""Where the CLI decides to put its log file.

Small surface, but it writes to disk before any command runs, so a wrong answer here
damages the output directory rather than merely logging to an odd place.
"""

from __future__ import annotations

import logging
from pathlib import Path

from assam_rolls import cli, log


class TestDefaultLogPath:
    def test_lands_under_out_logs(self, tmp_path):
        path = log.default_log_path(tmp_path)
        assert path.parent == tmp_path / "logs"
        assert path.name.startswith("run-") and path.suffix == ".log"

    def test_each_call_is_timestamped(self, tmp_path):
        assert "Z.log" in log.default_log_path(tmp_path).name


class TestWorkerInit:
    """The pool initializer's signature, pinned.

    Regression: ``render`` passed only ``(log_file,)``. Every worker died on startup with
    a TypeError, and ``multiprocessing`` respawned them forever -- the run neither
    progressed nor exited, and the tracebacks scrolled past inside a pipe. A crashing
    initializer has no natural failure signal, so it gets one here.
    """

    def test_accepts_the_arguments_the_pools_pass(self, tmp_path):
        log.worker_init(str(tmp_path / "w.log"), logging.WARNING)

    def test_accepts_a_missing_log_file(self):
        log.worker_init(None, logging.WARNING)

    def test_every_pool_passes_a_matching_initargs_tuple(self):
        """Read the source: each initargs tuple must satisfy worker_init's signature."""
        import inspect
        import re

        source = inspect.getsource(cli)
        required = len(
            [
                p
                for p in inspect.signature(log.worker_init).parameters.values()
                if p.default is inspect.Parameter.empty
            ]
        )
        tuples = re.findall(r"initargs=\(([^)]*)\)", source)
        assert tuples, "no pool initargs found; did the pools move?"
        for raw in tuples:
            supplied = len([part for part in raw.split(",") if part.strip()])
            assert supplied == required, f"initargs=({raw}) supplies {supplied}, need {required}"


class TestLogLocation:
    """``--out`` means something different per command, so logs do not follow it.

    Regressions this pins: ``review --out out/review.html`` created a *directory* where
    the HTML belonged, and ``render --out out/pages`` dropped a ``logs/`` directory in
    among 31,486 PNGs.
    """

    def resolve(self, argv, tmp_path, monkeypatch):
        """Run ``main`` far enough to fix the log path, without running the command.

        ``build_parser`` resolves the command functions when it executes -- inside
        ``main`` -- so replacing them on the module beforehand is what takes effect.
        """
        captured = {}

        def fake_setup(level, log_file, **kwargs):
            captured["log_file"] = log_file
            return logging.getLogger("test")

        monkeypatch.setattr(cli._log, "setup_logging", fake_setup)
        for command in ("cmd_review", "cmd_build", "cmd_ocr", "cmd_render"):
            monkeypatch.setattr(cli, command, lambda _args: 0)

        assert cli.main(argv) == 0
        return captured["log_file"]

    def test_logs_go_to_a_fixed_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["build", "--out", "dataset"], tmp_path, monkeypatch)
        assert path.parent == Path("out/logs")

    def test_a_file_out_does_not_become_a_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["review", "--out", "out/review.html"], tmp_path, monkeypatch)
        assert path.parent == Path("out/logs")
        assert not Path("out/review.html").is_dir()

    def test_a_data_directory_out_stays_clean(self, tmp_path, monkeypatch):
        """`render --out out/pages` must not put logs among the rendered pages."""
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["render", "--out", "out/pages"], tmp_path, monkeypatch)
        assert path.parent == Path("out/logs")
        assert not (Path("out/pages") / "logs").exists()

    def test_explicit_log_file_is_respected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["--log-file", "custom.log", "build"], tmp_path, monkeypatch)
        assert path == Path("custom.log")
