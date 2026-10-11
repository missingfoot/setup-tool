"""Setup Tool updating itself.

Its pkgver never changes, so "newer" means a different git commit: the
PKGBUILD stamps the commit it was built from into /usr/share/setup-tool/commit,
and that's compared with GitHub's HEAD. Run from a source checkout instead,
the checkout itself is the app - checking is a fetch, updating a pull.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Union

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from .installer import BUILD_ROOT, find_built_package

REPO_URL = "https://github.com/missingfoot/setup-tool.git"
BUILD_DIR = BUILD_ROOT / "setup-tool"

# A command, or a function producing one when it's reached - for steps that
# depend on an earlier one's result (the package file makepkg builds).
Command = Union[list[str], Callable[[], list[str]]]


def is_source_checkout(repo_root: Path) -> bool:
    return (repo_root / ".git").exists()


def packaged_commit(repo_root: Path) -> str | None:
    try:
        return (repo_root / "commit").read_text().strip() or None
    except OSError:
        return None


def parse_ls_remote(output: str) -> str | None:
    """The commit from `git ls-remote <url> HEAD` output, if it gave one."""
    for line in output.splitlines():
        commit, _, ref = line.partition("\t")
        if ref == "HEAD" and len(commit) == 40:
            return commit
    return None


def packaged_update_available(installed: str | None, remote: str | None) -> bool:
    """An install with no stamp predates self-updating, so it counts as
    out of date; a failed check (no remote commit) never does."""
    return remote is not None and installed != remote


def source_update_available(rev_list_count: str) -> bool:
    return rev_list_count.strip().isdigit() and int(rev_list_count) > 0


def check_commands(repo_root: Path) -> list[Command]:
    """Commands whose last output says whether an update exists - read it
    with source_update_available or parse_ls_remote, by mode."""
    if is_source_checkout(repo_root):
        root = str(repo_root)
        # Counting commits behind (not comparing HEADs) so unpushed local
        # commits don't show up as an "update".
        return [
            ["git", "-C", root, "fetch", "--quiet"],
            ["git", "-C", root, "rev-list", "--count", "HEAD..@{u}"],
        ]
    return [["git", "ls-remote", REPO_URL, "HEAD"]]


def update_commands(repo_root: Path) -> list[Command]:
    if is_source_checkout(repo_root):
        return [["git", "-C", str(repo_root), "pull", "--ff-only"]]

    def install() -> list[str]:
        package = find_built_package(BUILD_DIR)
        if package is None:
            raise FileNotFoundError(f"makepkg built no package in {BUILD_DIR}")
        # Absolute path: pkexec doesn't keep our working directory.
        return ["pkexec", "pacman", "-U", "--noconfirm", str(package)]

    # Same steps as any other git-hosted PKGBUILD app, build run unprivileged.
    return [
        ["rm", "-rf", str(BUILD_DIR)],
        ["git", "clone", "--depth", "1", REPO_URL, str(BUILD_DIR)],
        ["makepkg", "-f", "--noconfirm", "-D", str(BUILD_DIR)],
        install,
    ]


class CommandChain(QObject):
    """Runs commands one after another, stopping at the first failure.
    `finished` carries success and the last command's output (stdout and
    stderr merged)."""

    output = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, commands: list[Command], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._commands = list(commands)
        self._process: QProcess | None = None
        self._process_output = ""

    def start(self) -> None:
        self._next()

    def _next(self) -> None:
        if not self._commands:
            self.finished.emit(True, self._process_output)
            return
        command = self._commands.pop(0)
        if callable(command):
            try:
                command = command()
            except OSError as exc:
                self.finished.emit(False, str(exc))
                return
        self.output.emit(f"$ {' '.join(command)}")
        self._start(command)

    def _start(self, command: list[str]) -> None:
        self._process_output = ""
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        # Never sit waiting on a git credential prompt nobody can see.
        env = QProcessEnvironment.systemEnvironment()
        env.insert("GIT_TERMINAL_PROMPT", "0")
        process.setProcessEnvironment(env)
        process.errorOccurred.connect(lambda error: self._on_error(command, error))
        process.finished.connect(self._on_finished)
        self._process = process
        process.start(command[0], command[1:])

    def _read(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardOutput()).decode(errors="replace")
        self._process_output += text
        if text.rstrip():
            self.output.emit(text.rstrip())

    def _on_error(self, command: list[str], error: QProcess.ProcessError) -> None:
        # A command that can't start never emits `finished`.
        if error == QProcess.ProcessError.FailedToStart:
            self.finished.emit(False, f"couldn't run {command[0]}")

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.finished.emit(False, self._process_output)
            return
        self._next()
