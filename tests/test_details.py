from pathlib import Path

from setuptoollib.details import details_html
from setuptoollib.manifest import AppEntry, resolve_fallback

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_bundled_pkgbuild_shows_all_files_escaped(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "PKGBUILD").write_text('url="https://example.com"\npkgver=1 # <b>not html</b>\n')
    (tmp_path / "pkg" / "fix.patch").write_text("--- a\n+++ b\n")
    app = AppEntry(id="pkg", name="Pkg", category="C", description="d", method="pkgbuild", source="pkg")
    html = details_html(app, tmp_path, {})
    assert "makepkg" in html and "pacman -U" in html
    assert "&lt;b&gt;not html&lt;/b&gt;" in html
    assert html.index("PKGBUILD</h4>") < html.index("fix.patch</h4>")
    assert "https://example.com" in html


def test_repo_app_says_which_repo_and_services():
    app = AppEntry(id="p", name="P", category="C", description="", method="pacman",
                   package="pacman", services=("x.service",))
    html = details_html(app, REPO_ROOT, {"pacman": "core"})
    assert "<b>core</b>" in html
    assert "pacman -Syu --needed pacman" in html
    assert "x.service" in html


def test_unavailable_repo_app():
    app = AppEntry(id="p", name="P", category="C", description="", method="pacman", package="nope-xyz")
    assert "can't be installed here" in details_html(app, REPO_ROOT, {})


def test_fallback_pkgbuild_shown_for_review_either_way():
    app = next(a for a in __import__("setuptoollib.manifest").manifest.load_apps(REPO_ROOT / "apps.yaml")
               if a.fallback)
    in_repo = details_html(app, REPO_ROOT, {app.package: "cachyos"})
    assert "Fallback PKGBUILD" in in_repo and "PKGBUILD</h4>" in in_repo
    missing = details_html(resolve_fallback(app, {}), REPO_ROOT, {})
    assert "<b>fallback</b>" in missing and "PKGBUILD</h4>" in missing


def test_git_app_points_to_repo():
    app = AppEntry(id="g", name="G", category="C", description="", method="pkgbuild", git="https://example.com/g.git")
    html = details_html(app, REPO_ROOT, {})
    assert "https://example.com/g.git" in html and "run time" in html
