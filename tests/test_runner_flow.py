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


def test_installed_app_still_gets_missing_extras(runner, monkeypatch):
    monkeypatch.setattr(inst, "is_installed", lambda app: True)
    monkeypatch.setattr(inst, "missing_deps", lambda deps: [d for d in deps if d != "ffmpeg"])
    app = AppEntry(id="yt-dlp", name="yt-dlp", category="C", description="", method="pacman",
                   package="yt-dlp", extras=("ffmpeg", "aria2"))
    finished = []
    runner.app_finished.connect(lambda app_id, ok: finished.append(app_id))
    runner.run([app, _repo("vlc")])
    prompts = [e[1] for e in runner.events if e[0] == "pkexec"]
    assert prompts == ["pacman -Syu --needed --noconfirm && pacman -S --needed --asdeps --noconfirm aria2"]
    # vlc was done up front; yt-dlp waited for the privileged step
    assert finished == ["vlc", "yt-dlp"]


def _script(id, tmp_path):
    return AppEntry(id=id, name=id, category="C", description="", method="script",
                    script="https://example.com/install.sh", creates=str(tmp_path / "bin" / id))


def test_script_app_runs_installer_as_user_without_pkexec(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "is_installed", lambda app: app.method == "script" and app.creates_path.exists())
    app = _script("claude", tmp_path)
    ran = []

    def fake_command(command, cwd, on_finished):
        ran.append(command[0])
        if command[0] == "bash":  # the installer creates its file
            app.creates_path.parent.mkdir(parents=True, exist_ok=True)
            app.creates_path.write_text("")
        on_finished(command, 0, OK)

    monkeypatch.setattr(runner, "_run_command", fake_command)
    finished = []
    runner.app_finished.connect(lambda app_id, ok: finished.append((app_id, ok)))
    runner.run([app])
    assert ran == ["rm", "mkdir", "curl", "bash"]
    assert not [e for e in runner.events if e[0] == "pkexec"]
    assert finished == [("claude", True)] and runner.finished


def test_script_app_already_installed_is_skipped(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "is_installed", lambda app: True)
    ran = []
    monkeypatch.setattr(runner, "_run_command", lambda c, cwd, on_finished: ran.append(c))
    runner.run([_script("claude", tmp_path)])
    assert ran == [] and runner.finished


def test_script_app_fails_if_installer_creates_nothing(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "is_installed", lambda app: False)
    monkeypatch.setattr(runner, "_run_command", lambda c, cwd, on_finished: on_finished(c, 0, OK))
    finished = []
    runner.app_finished.connect(lambda app_id, ok: finished.append((app_id, ok)))
    runner.run([_script("claude", tmp_path)])
    assert finished == [("claude", False)]
