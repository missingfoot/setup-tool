"""root_commands, user_services and login: the setup an app needs beyond
installing, done in the run instead of left as next steps."""
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

import setuptoollib.installer as inst
from setuptoollib.manifest import AppEntry, Login, RootCommand, load_apps

OK = QProcess.ExitStatus.NormalExit
REPO_ROOT = Path(__file__).resolve().parent.parent

OPERATOR = RootCommand(run="tailscale set --operator={user}", unless="check-operator {user}")
LOGIN = Login(check="tailscale status", run="tailscale login")


def _app(id="tailscale", **kw):
    return AppEntry(id=id, name=id, category="C", description="", method="pacman", package=id, **kw)


# --- manifest -------------------------------------------------------------

def test_new_fields_load(tmp_path):
    manifest = tmp_path / "apps.yaml"
    manifest.write_text(
        "- id: t\n  name: T\n  category: C\n  description: d\n  method: pacman\n  package: t\n"
        "  root_commands:\n    - run: do-it {user}\n      unless: done-already\n    - run: always\n"
        "  user_services: [a.service]\n"
        "  login:\n    check: is-in\n    run: log-in\n"
    )
    (app,) = load_apps(manifest)
    assert app.root_commands == (RootCommand("do-it {user}", "done-already"), RootCommand("always"))
    assert app.user_services == ("a.service",)
    assert app.login == Login("is-in", "log-in")


def test_new_fields_default_empty():
    app = _app("x")
    assert (app.root_commands, app.user_services, app.login) == ((), (), None)


def test_real_manifest_tailscale_and_taildrop_share_setup():
    apps = {a.id: a for a in load_apps(REPO_ROOT / "apps.yaml")}
    tailscale, taildrop = apps["tailscale"], apps["taildrop-send"]
    assert tailscale.root_commands and tailscale.root_commands == taildrop.root_commands
    assert tailscale.login and tailscale.login == taildrop.login
    assert taildrop.user_services == ("taildrop.service",)
    assert "tailscaled.service" in taildrop.services


# --- helpers --------------------------------------------------------------

def test_fill_user(monkeypatch):
    monkeypatch.setattr(inst.getpass, "getuser", lambda: "james")
    assert inst.fill_user("tailscale set --operator={user}") == "tailscale set --operator=james"


def test_fill_user_rejects_odd_usernames(monkeypatch):
    monkeypatch.setattr(inst.getpass, "getuser", lambda: "a b;rm")
    with pytest.raises(ValueError):
        inst.fill_user("x {user}")


def test_root_command_without_unless_always_runs():
    assert not inst.root_command_done(RootCommand("anything"))


def test_root_command_done_runs_unless_check(monkeypatch):
    monkeypatch.setattr(inst.getpass, "getuser", lambda: "james")
    assert inst.root_command_done(RootCommand("x", unless="[ {user} = james ]"))
    assert not inst.root_command_done(RootCommand("x", unless="[ {user} = bob ]"))


def test_privileged_script_root_commands_last(monkeypatch):
    monkeypatch.setattr(inst.getpass, "getuser", lambda: "james")
    script = inst.privileged_script([], ["tailscale"], ["tailscaled.service"], root_commands=[OPERATOR])
    assert script == (
        "pacman -Syu --needed --noconfirm tailscale && systemctl enable --now tailscaled.service"
        " && tailscale set --operator=james"
    )


def test_find_url_in_login_output():
    text = "\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123\n\n"
    assert inst.find_url(text) == "https://login.tailscale.com/a/abc123"
    assert inst.find_url("nothing here") is None


# --- runner flow ----------------------------------------------------------

@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(inst, "BUILD_ROOT", tmp_path)
    monkeypatch.setattr(inst.getpass, "getuser", lambda: "james")
    monkeypatch.setattr(inst, "service_is_running", lambda unit: True)
    monkeypatch.setattr(inst, "is_installed", lambda app: True)
    monkeypatch.setattr(inst, "missing_deps", lambda deps: [])
    state = {"operator": False, "user_running": set(), "logged_in": False}
    monkeypatch.setattr(inst, "root_command_done", lambda cmd: state["operator"])
    monkeypatch.setattr(inst, "user_service_is_running", lambda unit: unit in state["user_running"])
    monkeypatch.setattr(inst, "login_done", lambda login: state["logged_in"])
    opened = []
    monkeypatch.setattr(inst, "open_url", opened.append)

    r = inst.InstallRunner(Path("."))
    r.events, r.state, r.opened = [], state, opened

    def fake_command(command, cwd, on_finished, on_output=None):
        r.events.append(("user", command))
        if command[:3] == ["systemctl", "--user", "enable"]:
            state["user_running"].update(command[4:])
        if command == ["tailscale", "login"]:
            on_output("To authenticate, visit:\n\n\thttps://login.tailscale.com/a/xyz\n")
            state["logged_in"] = True
        on_finished(command, 0, OK)

    def fake_privileged(script, on_finished):
        r.events.append(("pkexec", script))
        if "--operator" in script:
            state["operator"] = True
        on_finished(0, OK)

    monkeypatch.setattr(r, "_run_command", fake_command)
    monkeypatch.setattr(r, "_run_privileged", fake_privileged)
    r.results = []
    r.app_finished.connect(lambda app_id, ok: r.results.append((app_id, ok)))
    return r


def test_fresh_setup_runs_everything_in_order(runner):
    app = _app(root_commands=(OPERATOR,), user_services=("taildrop.service",), login=LOGIN)
    runner.run([app])
    assert runner.events == [
        ("pkexec", "tailscale set --operator=james"),
        ("user", ["systemctl", "--user", "daemon-reload"]),
        ("user", ["systemctl", "--user", "enable", "--now", "taildrop.service"]),
        ("user", ["tailscale", "login"]),
    ]
    assert runner.opened == ["https://login.tailscale.com/a/xyz"]
    assert runner.results == [("tailscale", True)]


def test_already_set_up_does_nothing_and_asks_no_password(runner):
    runner.state.update(operator=True, user_running={"taildrop.service"}, logged_in=True)
    app = _app(root_commands=(OPERATOR,), user_services=("taildrop.service",), login=LOGIN)
    runner.run([app])
    assert runner.events == []
    assert runner.results == [("tailscale", True)]


def test_shared_steps_run_once_for_two_apps(runner):
    a = _app("tailscale", root_commands=(OPERATOR,), login=LOGIN)
    b = _app("taildrop-send", root_commands=(OPERATOR,), login=LOGIN, user_services=("taildrop.service",))
    runner.run([a, b])
    pkexec = [e for e in runner.events if e[0] == "pkexec"]
    logins = [e for e in runner.events if e == ("user", ["tailscale", "login"])]
    assert pkexec == [("pkexec", "tailscale set --operator=james")]
    assert len(logins) == 1
    assert sorted(runner.results) == [("taildrop-send", True), ("tailscale", True)]


def test_login_that_does_not_finish_fails_the_app(runner, monkeypatch):
    runner.state.update(operator=True)

    def no_login(command, cwd, on_finished, on_output=None):
        runner.events.append(("user", command))
        on_finished(command, 1, OK)  # e.g. timed out and was killed

    monkeypatch.setattr(runner, "_run_command", no_login)
    runner.run([_app(login=LOGIN)])
    assert runner.results == [("tailscale", False)]


def test_failed_privileged_step_skips_user_steps(runner, monkeypatch):
    def fail(script, on_finished):
        runner.events.append(("pkexec", script))
        on_finished(1, OK)

    monkeypatch.setattr(runner, "_run_privileged", fail)
    runner.run([_app(root_commands=(OPERATOR,), user_services=("taildrop.service",), login=LOGIN)])
    assert [e[0] for e in runner.events] == ["pkexec"]
    assert runner.results == [("tailscale", False)]
