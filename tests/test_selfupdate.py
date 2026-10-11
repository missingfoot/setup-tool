from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

import setuptoollib.selfupdate as su

OK = QProcess.ExitStatus.NormalExit
A = "a" * 40
B = "b" * 40


def test_parse_ls_remote_takes_head_commit():
    assert su.parse_ls_remote(f"{A}\tHEAD\n") == A


def test_parse_ls_remote_rejects_junk():
    assert su.parse_ls_remote("") is None
    assert su.parse_ls_remote("fatal: unable to access\n") is None


def test_is_source_checkout(tmp_path):
    assert not su.is_source_checkout(tmp_path)
    (tmp_path / ".git").mkdir()
    assert su.is_source_checkout(tmp_path)


def test_packaged_commit_read_from_stamp(tmp_path):
    assert su.packaged_commit(tmp_path) is None  # pre-self-update installs have no stamp
    (tmp_path / "commit").write_text(f"{A}\n")
    assert su.packaged_commit(tmp_path) == A


def test_packaged_update_available():
    assert su.packaged_update_available(A, B)
    assert not su.packaged_update_available(A, A)
    assert not su.packaged_update_available(A, None)  # check failed: say nothing
    assert su.packaged_update_available(None, B)  # unstamped install: rebuild it


def test_source_update_available_counts_commits_behind():
    assert su.source_update_available("3\n")
    assert not su.source_update_available("0\n")
    assert not su.source_update_available("")


def test_check_commands_packaged(tmp_path):
    assert su.check_commands(tmp_path) == [["git", "ls-remote", su.REPO_URL, "HEAD"]]


def test_check_commands_source_fetches_then_counts(tmp_path):
    (tmp_path / ".git").mkdir()
    root = str(tmp_path)
    assert su.check_commands(tmp_path) == [
        ["git", "-C", root, "fetch", "--quiet"],
        ["git", "-C", root, "rev-list", "--count", "HEAD..@{u}"],
    ]


def test_update_commands_source_is_a_fast_forward_pull(tmp_path):
    (tmp_path / ".git").mkdir()
    assert su.update_commands(tmp_path) == [["git", "-C", str(tmp_path), "pull", "--ff-only"]]


def test_update_commands_packaged_clone_build_install(tmp_path, monkeypatch):
    build = tmp_path / "build" / "setup-tool"
    monkeypatch.setattr(su, "BUILD_DIR", build)
    commands = su.update_commands(tmp_path / "usr-share")
    assert commands[0] == ["rm", "-rf", str(build)]
    assert commands[1] == ["git", "clone", "--depth", "1", su.REPO_URL, str(build)]
    assert commands[2] == ["makepkg", "-f", "--noconfirm", "-D", str(build)]
    # The package file only exists after makepkg, so the last step is resolved late.
    build.mkdir(parents=True)
    (build / "setup-tool-0.1.0-1-any.pkg.tar.zst").write_text("")
    assert commands[3]() == ["pkexec", "pacman", "-U", "--noconfirm", str(build / "setup-tool-0.1.0-1-any.pkg.tar.zst")]


def test_update_commands_packaged_fails_cleanly_without_package(tmp_path, monkeypatch):
    build = tmp_path / "build"
    build.mkdir()
    monkeypatch.setattr(su, "BUILD_DIR", build)
    with pytest.raises(FileNotFoundError):
        su.update_commands(tmp_path / "usr-share")[3]()


class _FakeChain(su.CommandChain):
    """Records commands instead of running them; scripted exit codes/output."""

    def __init__(self, commands, results):
        super().__init__(commands)
        self.ran = []
        self._results = list(results)

    def _start(self, command):
        self.ran.append(command)
        code, out = self._results.pop(0)
        self._process_output = out
        self._on_finished(code, OK)


def test_chain_runs_all_and_reports_last_output():
    done = []
    chain = _FakeChain([["a"], lambda: ["b"]], [(0, ""), (0, "out-b")])
    chain.finished.connect(lambda ok, out: done.append((ok, out)))
    chain.start()
    assert chain.ran == [["a"], ["b"]]
    assert done == [(True, "out-b")]


def test_chain_stops_at_first_failure():
    done = []
    chain = _FakeChain([["a"], ["b"]], [(1, "boom")])
    chain.finished.connect(lambda ok, out: done.append((ok, out)))
    chain.start()
    assert chain.ran == [["a"]]
    assert done == [(False, "boom")]


def test_chain_reports_late_command_errors_as_failure():
    def missing():
        raise FileNotFoundError("no package built")

    done = []
    chain = _FakeChain([missing], [])
    chain.finished.connect(lambda ok, out: done.append((ok, out)))
    chain.start()
    assert done == [(False, "no package built")]
