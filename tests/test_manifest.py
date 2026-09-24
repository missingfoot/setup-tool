from pathlib import Path

import pytest

from setuptoollib.manifest import AppEntry, load_apps, resolve_fallback

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_real_manifest():
    apps = load_apps(REPO_ROOT / "apps.yaml")
    assert apps
    ids = [app.id for app in apps]
    assert "sublime-text-4" in ids
    assert len(ids) == len(set(ids))


def test_pacman_method_requires_package():
    with pytest.raises(ValueError):
        AppEntry(id="x", name="X", category="C", description="", method="pacman")


def test_pkgbuild_method_requires_source_or_git():
    with pytest.raises(ValueError):
        AppEntry(id="x", name="X", category="C", description="", method="pkgbuild")

    # either one alone is fine
    AppEntry(id="x", name="X", category="C", description="", method="pkgbuild", source="pkgbuilds/x")
    AppEntry(id="x", name="X", category="C", description="", method="pkgbuild", git="https://example.com/x.git")


def test_unknown_method_rejected():
    with pytest.raises(ValueError):
        AppEntry(id="x", name="X", category="C", description="", method="flatpak")


def test_services_and_next_steps_load(tmp_path):
    manifest = tmp_path / "apps.yaml"
    manifest.write_text(
        "- id: x\n  name: X\n  category: C\n  description: ''\n  method: pacman\n  package: x\n"
        "  services: [x.service]\n  next_steps: run x\n"
    )
    [app] = load_apps(manifest)
    assert app.services == ("x.service",)
    assert app.next_steps == "run x"


def test_services_default_empty():
    app = AppEntry(id="x", name="X", category="C", description="", method="pacman", package="x")
    assert app.services == ()
    assert app.next_steps is None


def _fallback_app(**kw):
    return AppEntry(id="x", name="X", category="C", description="", method="pacman",
                    package="x-bin", fallback="pkgbuilds/x-bin", **kw)


def test_resolve_fallback_keeps_pacman_when_in_a_repo():
    app = _fallback_app()
    assert resolve_fallback(app, {"x-bin": "cachyos"}) is app


def test_resolve_fallback_switches_to_pkgbuild_when_missing():
    app = resolve_fallback(_fallback_app(), {})
    assert app.method == "pkgbuild"
    assert app.source == "pkgbuilds/x-bin"
    assert app.package == "x-bin"
    assert app.using_fallback


def test_fallback_only_for_pacman_method():
    with pytest.raises(ValueError):
        AppEntry(id="x", name="X", category="C", description="", method="pkgbuild",
                 source="s", fallback="f")


def test_real_manifest_fallbacks_exist():
    for app in load_apps(REPO_ROOT / "apps.yaml"):
        if app.fallback:
            assert (REPO_ROOT / app.fallback / "PKGBUILD").is_file(), app.id
