from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str
    category: str
    description: str
    method: str  # "pacman", "pkgbuild" or "script"
    package: str | None = None  # pacman: the package name to install
    source: str | None = None  # pkgbuild: dir with the PKGBUILD, relative to repo root
    git: str | None = None  # pkgbuild: git remote to clone instead of `source`
    services: tuple[str, ...] = ()  # systemd units to `enable --now` after install
    extras: tuple[str, ...] = ()  # repo packages (optional deps) installed alongside, as deps
    next_steps: str | None = None  # manual follow-up shown after a successful run
    fallback: str | None = None  # pacman: bundled PKGBUILD dir used when no sync repo has `package`
    script: str | None = None  # script: URL of the vendor's own installer, run as the user
    creates: str | None = None  # script: file the installer creates (~ allowed) - there means installed

    def __post_init__(self) -> None:
        if self.method not in ("pacman", "pkgbuild", "script"):
            raise ValueError(f"{self.id}: unknown method {self.method!r}")
        if self.method == "pacman" and not self.package:
            raise ValueError(f"{self.id}: method 'pacman' requires 'package'")
        if self.method == "pkgbuild" and not (self.source or self.git):
            raise ValueError(f"{self.id}: method 'pkgbuild' requires 'source' or 'git'")
        if self.method == "script" and not (self.script and self.creates):
            raise ValueError(f"{self.id}: method 'script' requires 'script' and 'creates'")
        if self.script and not self.script.startswith("https://"):
            raise ValueError(f"{self.id}: 'script' must be an https:// URL")
        # (A resolved fallback is a pkgbuild entry whose source *is* the fallback.)
        if self.fallback and self.method != "pacman" and self.source != self.fallback:
            raise ValueError(f"{self.id}: 'fallback' only applies to method 'pacman'")

    @property
    def creates_path(self) -> Path | None:
        return Path(self.creates).expanduser() if self.creates else None

    @property
    def using_fallback(self) -> bool:
        return self.method == "pkgbuild" and self.fallback is not None


def resolve_fallback(app: AppEntry, repos: dict[str, str]) -> AppEntry:
    """A pacman app whose package no sync repo has (e.g. a [cachyos]-only
    package on plain Arch) becomes a build of its bundled fallback PKGBUILD.
    `package` stays set, so installed-version checks still use its name."""
    if app.method == "pacman" and app.fallback and app.package not in repos:
        return replace(app, method="pkgbuild", source=app.fallback)
    return app


def load_apps(manifest_path: Path) -> list[AppEntry]:
    data = yaml.safe_load(manifest_path.read_text()) or []
    return [
        AppEntry(**{
            **entry,
            "services": tuple(entry.get("services") or ()),
            "extras": tuple(entry.get("extras") or ()),
        })
        for entry in data
    ]
