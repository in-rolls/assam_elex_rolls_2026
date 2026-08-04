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


class TestOutDirResolution:
    """``--out`` means a directory for most commands and a *file* for ``review``."""

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

    def test_directory_out_gets_a_logs_subdirectory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["build", "--out", "dataset"], tmp_path, monkeypatch)
        assert path.parent == Path("dataset/logs")

    def test_file_out_logs_beside_it_not_inside_it(self, tmp_path, monkeypatch):
        """Regression: ``review --out out/review.html`` created a *directory* there."""
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["review", "--out", "out/review.html"], tmp_path, monkeypatch)
        assert path.parent == Path("out/logs")
        assert not Path("out/review.html").is_dir()

    def test_explicit_log_file_is_respected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = self.resolve(["--log-file", "custom.log", "build"], tmp_path, monkeypatch)
        assert path == Path("custom.log")
