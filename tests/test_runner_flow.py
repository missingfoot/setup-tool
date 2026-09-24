"""Drives InstallRunner's phase ordering with every external command stubbed
out, so it runs anywhere and never touches the real system."""
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

import setuptoollib.installer as inst
from setuptoollib.manifest import AppEntry

OK = QProcess.ExitStatus.NormalExit


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "BUILD_ROOT", tmp_path)
    monkeypatch.setattr(inst, "service_is_running", lambda unit: True)
    monkeypatch.setattr(inst, "is_installed", lambda app: False)
    monkeypatch.setattr(inst, "installed_version", lambda app: None)
    monkeypatch.setattr(inst, "pkgbuild_target_version", lambda d: "1-1")
    monkeypatch.setattr(inst, "pkgbuild_build_deps", lambda d: ["dep-of-" + d.name])

    r = inst.InstallRunner(Path("."))
    r.events = []

    def fake_command(command, cwd, on_finished):
        if command[0] == "makepkg":
            r.events.append(("build", cwd.name))
            (cwd / f"{cwd.name}-1-1-x86_64.pkg.tar.zst").write_text("")
        on_finished(command, 0, OK)

    def fake_privileged(script, on_finished):
        r.events.append(("pkexec", script))
        on_finished(0, OK)

    monkeypatch.setattr(r, "_run_command", lambda c, cwd, on_finished: (
        (cwd or tmp_path).mkdir(parents=True, exist_ok=True), fake_command(c, cwd or tmp_path, on_finished)))
    monkeypatch.setattr(r, "_run_privileged", fake_privileged)
    r.finished = []
    r.all_finished.connect(lambda: r.finished.append(True))
    return r


def _pkg(id):
    return AppEntry(id=id, name=id, category="C", description="", method="pkgbuild", source=f"pkgbuilds/{id}")


def _repo(id):
    return AppEntry(id=id, name=id, category="C", description="", method="pacman", package=id)


def test_one_prompt_when_build_deps_present(runner, monkeypatch):
    monkeypatch.setattr(inst, "missing_deps", lambda deps: [])
    runner.run([_repo("vlc"), _pkg("qdirstat")])
    prompts = [e for e in runner.events if e[0] == "pkexec"]
    assert len(prompts) == 1
    assert "pacman -Syu --needed --noconfirm vlc" in prompts[0][1]
    assert "pacman -U" in prompts[0][1]
    assert runner.events.index(("build", "qdirstat")) < runner.events.index(prompts[0])
    assert runner.finished


def test_missing_build_deps_installed_before_building(runner, monkeypatch):
    monkeypatch.setattr(inst, "missing_deps", lambda deps: list(deps))
    runner.run([_repo("vlc"), _pkg("qdirstat"), _pkg("fsearch")])
    kinds = [e[0] for e in runner.events]
    assert kinds == ["pkexec", "build", "build", "pkexec"]
    deps_script, final_script = runner.events[0][1], runner.events[3][1]
    # Repo apps ride along with the build deps prompt, not the final one.
    assert "vlc" in deps_script and "vlc" not in final_script
    assert "--asdeps --noconfirm dep-of-qdirstat dep-of-fsearch" in deps_script
    assert "pacman -U" in final_script
    assert runner.finished
