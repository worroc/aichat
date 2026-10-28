"""The start line in a service log: written once per start, found by pid."""

from datetime import UTC, datetime, timedelta

from aichat_server.control import _version
from aichat_server.lifecycle import Status
from aichat_server.startlog import StartInfo, current_version, find_start, start_line

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def test_find_start_takes_the_line_for_the_running_pid(tmp_path):
    log = tmp_path / "svc.log"
    log.write_text("\n".join([
        start_line(10, "0.1.0+aaaaaaa", T0),
        "INFO some request",
        start_line(20, "0.1.0+bbbbbbb", T0 + timedelta(hours=1)),
        "aichat-start: pid=20 broken",
        "",
    ]))
    assert find_start(log, 20) == StartInfo(20, "0.1.0+bbbbbbb", T0 + timedelta(hours=1))
    assert find_start(log, 10).version == "0.1.0+aaaaaaa"
    assert find_start(log, 30) is None
    assert find_start(tmp_path / "missing.log", 10) is None


def test_current_version_has_the_git_commit():
    version = current_version()
    assert version.startswith("0.1.0+") and len(version.split("+")[1]) >= 7


def test_version_column_marks_old_code():
    running = Status("up", 1, StartInfo(1, "0.1.0+aaaaaaa", T0))
    assert _version(running, "0.1.0+aaaaaaa") == "0.1.0+aaaaaaa"
    assert _version(running, "0.1.0+bbbbbbb") == "0.1.0+aaaaaaa (old)"
    assert _version(Status("up", 1), "x") == "?"
    assert _version(Status("down", None), "x") == ""
