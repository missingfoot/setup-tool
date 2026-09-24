"""The details side panel: what Run will do for an app, and the full text of
any PKGBUILD (plus patches/scripts next to it) so it can be reviewed before
installing."""
from __future__ import annotations

import subprocess
from html import escape
from pathlib import Path

from .installer import installed_version, pkgbuild_url
from .manifest import AppEntry

# Anything bigger than this in a PKGBUILD dir isn't something to read inline.
MAX_FILE_BYTES = 200_000

PLACEHOLDER = "<p><i>Select an app to see what installing it does, and review its PKGBUILD.</i></p>"


def repo_info(package: str) -> dict[str, str]:
    """A few `pacman -Si` fields for a sync-repo package ({} if unknown)."""
    result = subprocess.run(["pacman", "-Si", package], capture_output=True, text=True)
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(" : ")
        key = key.strip()
        if sep and key not in info:  # first entry = the repo pacman would use
            info[key] = value.strip()
    return info


def _files_html(folder: Path) -> str:
    """Every file in a PKGBUILD dir, PKGBUILD first, as escaped <pre> blocks."""
    files = sorted((p for p in folder.rglob("*") if p.is_file()), key=lambda p: (p.name != "PKGBUILD", str(p)))
    parts = []
    for path in files:
        name = escape(str(path.relative_to(folder)))
        if path.stat().st_size > MAX_FILE_BYTES:
            parts.append(f"<h4>{name}</h4><p><i>{path.stat().st_size:,} bytes - too large to show here.</i></p>")
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            parts.append(f"<h4>{name}</h4><p><i>Binary file, not shown.</i></p>")
            continue
        parts.append(f"<h4>{name}</h4><pre>{escape(text)}</pre>")
    return "".join(parts)


def _link(url: str) -> str:
    return f'<a href="{escape(url)}">{escape(url)}</a>'


def _code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def details_html(app: AppEntry, repo_root: Path, repos: dict[str, str]) -> str:
    parts = [f"<h2>{escape(app.name)}</h2><p>{escape(app.description)}</p>"]
    facts: list[tuple[str, str]] = []
    installed = installed_version(app)
    facts.append(("Installed", escape(installed) if installed else "no"))

    parts.append("<h3>What Run does</h3><ul>")
    if app.method == "pacman":
        repo = repos.get(app.package)
        if repo is None:
            parts.append(
                f"<li>Nothing: {_code(app.package)} isn't in any of your sync repos and there's "
                "no fallback PKGBUILD, so it can't be installed here.</li>"
            )
        else:
            parts.append(
                f"<li>Installs {_code(app.package)} from the official <b>{escape(repo)}</b> repo with "
                f"{_code('pacman -Syu --needed ' + app.package)} (this also upgrades the system).</li>"
            )
            info = repo_info(app.package)
            for key in ("Version", "URL", "Licenses", "Packager", "Build Date"):
                if key in info:
                    facts.append((f"Repo {key.lower()}" if key == "Version" else key,
                                  _link(info[key]) if key == "URL" else escape(info[key])))
    elif app.git:
        parts.append(
            f"<li>Clones {_link(app.git)} and builds its PKGBUILD as your user with "
            f"{_code('makepkg')}, then installs the result with {_code('pacman -U')}.</li>"
            "<li>The PKGBUILD is only fetched at run time - review it in that repo first.</li>"
        )
    else:
        if app.using_fallback:
            parts.append(
                f"<li>{_code(app.package)} isn't in any of your sync repos, so this uses the "
                "bundled <b>fallback</b> PKGBUILD instead.</li>"
            )
        parts.append(
            f"<li>Builds the bundled PKGBUILD ({_code(app.source)}) as your user with "
            f"{_code('makepkg')}, then installs the result with {_code('pacman -U')}. "
            "Skipped if the installed version already matches.</li>"
            "<li>Any missing build dependencies are installed from the repos first.</li>"
        )
        url = pkgbuild_url(repo_root / app.source / "PKGBUILD")
        if url:
            facts.append(("Upstream", _link(url)))
    if app.services:
        units = ", ".join(_code(u) for u in app.services)
        parts.append(f"<li>Enables and starts {units} ({_code('systemctl enable --now')}).</li>")
    parts.append("</ul>")

    rows = "".join(f"<tr><td><b>{k}</b>&nbsp;&nbsp;</td><td>{v}</td></tr>" for k, v in facts)
    parts.append(f"<table>{rows}</table>")

    if app.next_steps:
        parts.append(f"<h3>Afterwards, in a terminal</h3><pre>{escape(app.next_steps.strip())}</pre>")

    if app.method == "pkgbuild" and app.source:
        parts.append(f"<h3>PKGBUILD files ({escape(app.source)})</h3>")
        parts.append(_files_html(repo_root / app.source))
    elif app.fallback:
        parts.append(
            f"<h3>Fallback PKGBUILD ({escape(app.fallback)})</h3>"
            "<p><i>Only used on systems where no sync repo has this package.</i></p>"
        )
        parts.append(_files_html(repo_root / app.fallback))

    return "".join(parts)
