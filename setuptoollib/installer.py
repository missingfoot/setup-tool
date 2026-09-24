from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .manifest import AppEntry

BUILD_ROOT = Path.home() / ".cache" / "setup-tool" / "build"


def build_dir_for(app: AppEntry) -> Path:
    return BUILD_ROOT / app.id


def pacman_name(app: AppEntry) -> str:
    """The name to look up in the local pacman database. `package` is
    authoritative when set (pacman method); pkgbuild-method apps fall back
    to `id`, which matches pkgname for every entry in apps.yaml so far."""
    return app.package or app.id


def installed_version(app: AppEntry) -> str | None:
    result = subprocess.run(
        ["pacman", "-Q", pacman_name(app)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.split()[1]


def is_installed(app: AppEntry) -> bool:
    return installed_version(app) is not None


def service_is_running(unit: str) -> bool:
    """Enabled for boot AND active now - i.e. `enable --now` has nothing left to do."""
    enabled = subprocess.run(["systemctl", "is-enabled", "--quiet", unit]).returncode == 0
    active = subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
    return enabled and active


def pkgbuild_target_version(build_dir: Path) -> str:
    """The version this PKGBUILD would produce, without building it.
    Sourcing a PKGBUILD only runs its top-level variable assignments -
    never build()/package() etc, those only run when makepkg calls them -
    so this is safe to do even before any source has been fetched."""
    script = f'source {shlex.quote(str(build_dir / "PKGBUILD"))}; echo "${{epoch:+$epoch:}}$pkgver-$pkgrel"'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=build_dir)
    return result.stdout.strip()


def pkgbuild_build_deps(build_dir: Path) -> list[str]:
    """Everything makepkg insists is installed before it will build:
    depends, makedepends and checkdepends, plus their _$CARCH variants.
    Read the same safe way as pkgbuild_target_version. (Dependencies a
    PKGBUILD only sets inside package() aren't checked by makepkg -
    `pacman -U` pulls those in at install time.)"""
    script = (
        f"source {shlex.quote(str(build_dir.resolve() / 'PKGBUILD'))}; "
        'CARCH=$(uname -m); for v in depends makedepends checkdepends; do '
        'for n in "$v" "${v}_$CARCH"; do a="$n[@]"; printf "%s\\n" "${!a}"; done; done'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=build_dir)
    return [line for line in result.stdout.splitlines() if line.strip()]


def missing_deps(deps: list[str]) -> list[str]:
    """The subset of `deps` not satisfied by installed packages. `pacman -T`
    understands provides (e.g. libgtk-3.so=0) and version constraints."""
    if not deps:
        return []
    result = subprocess.run(["pacman", "-T", *deps], capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def pkgbuild_url(pkgbuild: Path) -> str:
    """The PKGBUILD's upstream `url`, read the same safe way as
    pkgbuild_target_version."""
    script = f'source {shlex.quote(str(pkgbuild))}; echo "$url"'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=pkgbuild.parent)
    return result.stdout.strip()


def sync_repos(packages: list[str]) -> dict[str, str]:
    """Which sync repo pacman would install each package from, honouring
    pacman.conf's repo order (e.g. cachyos-extra-v3 over extra). One pacman
    call for the lot; if any name is unknown that call fails outright, so
    fall back to asking one at a time. Unknown packages are left out."""

    def lookup(names: list[str]) -> dict[str, str] | None:
        result = subprocess.run(
            ["pacman", "-Sp", "--print-format", "%n %r", *names],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        # -Sp also lists any not-yet-installed dependencies; keep only ours.
        pairs = (line.split() for line in result.stdout.splitlines())
        return {name: repo for name, repo in pairs if name in names}

    if not packages:
        return {}
    repos = lookup(packages)
    if repos is not None:
        return repos
    return {name: repo for package in packages for name, repo in (lookup([package]) or {}).items()}


def source_label(app: AppEntry, repo_root: Path, repos: dict[str, str]) -> tuple[str, str]:
    """(short text, tooltip) saying where an app comes from."""
    if app.method == "pacman":
        repo = repos.get(app.package)
        if repo is None:
            return "pacman", f"{app.package} - not found in any sync repo"
        return repo, f"Official {repo} repo (pacman package {app.package})"
    if app.git:
        return "Git PKGBUILD", f"Own PKGBUILD cloned from {app.git}"
    url = pkgbuild_url(repo_root / app.source / "PKGBUILD")
    host = url.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    text = f"PKGBUILD · {host}" if host else "PKGBUILD"
    if app.using_fallback:
        return text, (
            f"{app.package} isn't in any of your sync repos, so this builds the "
            f"bundled fallback PKGBUILD ({app.source}), upstream {url}"
        )
    return text, f"Own PKGBUILD ({app.source}), upstream {url}"


def prepare_commands(app: AppEntry, repo_root: Path) -> list[list[str]]:
    """Commands that put a fresh, unprivileged copy of the PKGBUILD + source
    into this app's build dir. Always wipes the build dir first so a retry
    never mixes stale files with a new attempt."""
    build_dir = build_dir_for(app)
    commands = [["rm", "-rf", str(build_dir)]]
    if app.git:
        commands.append(["git", "clone", "--depth", "1", app.git, str(build_dir)])
    else:
        source_dir = (repo_root / app.source).resolve()
        commands.append(["mkdir", "-p", str(build_dir)])
        commands.append(["cp", "-r", f"{source_dir}/.", str(build_dir)])
    return commands


def makepkg_command() -> list[str]:
    # Never run as root: makepkg refuses to, and this is where each
    # PKGBUILD's own source fetch + verification (e.g. Sublime's GPG check)
    # actually happens.
    return ["makepkg", "-f", "--noconfirm"]


def find_built_package(build_dir: Path) -> Path | None:
    matches = sorted(build_dir.glob("*.pkg.tar.*"))
    return matches[-1] if matches else None


def build_deps_script(pacman_installs: list[str], build_deps: list[str]) -> str:
    """The extra privileged step, run before building, only when some
    PKGBUILD's build dependencies are missing (e.g. on a fresh machine).
    The run's repo apps come along in this same prompt, so a run never
    asks for a password more than twice. `-Syu` first for the same reason
    as privileged_script; build deps go in `--asdeps` so they show up as
    orphans (`pacman -Qdtq`) if the app that needed them is removed."""
    names = " ".join(shlex.quote(n) for n in pacman_installs)
    deps = " ".join(shlex.quote(d) for d in build_deps)
    upgrade = f"pacman -Syu --needed --noconfirm {names}".rstrip()
    return f"{upgrade} && pacman -S --needed --asdeps --noconfirm {deps}"


def privileged_script(pkg_files: list[Path], pacman_installs: list[str], services: list[str] = ()) -> str:
    """The single bash script run under pkexec for a whole run.

    Repo installs use `-Syu`, never plain `-S`: a stale local sync db points
    at package versions the mirrors have already deleted (404 on download),
    and refreshing with `-Sy` alone would be a partial upgrade, which Arch
    doesn't support. So refreshing means upgrading the whole system too.
    It runs before `-U` so freshly built packages install against the
    upgraded libraries. Services are enabled last, once their packages exist."""
    parts = []
    if pacman_installs:
        names = " ".join(shlex.quote(n) for n in pacman_installs)
        parts.append(f"pacman -Syu --needed --noconfirm {names}")
    if pkg_files:
        files = " ".join(shlex.quote(str(p)) for p in pkg_files)
        parts.append(f"pacman -U --noconfirm {files}")
    if services:
        units = " ".join(shlex.quote(u) for u in services)
        parts.append(f"systemctl enable --now {units}")
    return " && ".join(parts)


# CachyOS's post-transaction hook prints this after kernel/core upgrades.
_REBOOT_MARKER = "Reboot is recommended"
MODULES_ROOT = Path("/usr/lib/modules")


def running_kernel_modules_missing(modules_root: Path = MODULES_ROOT) -> bool:
    """True once a kernel upgrade has deleted the running kernel's modules:
    anything that loads a module on demand (e.g. Mullvad's nftables firewall
    rules) fails until reboot. Works whatever the hook printed."""
    release = subprocess.run(["uname", "-r"], capture_output=True, text=True).stdout.strip()
    return bool(release) and not (modules_root / release).is_dir()


def reboot_needed(output: str) -> bool:
    return _REBOOT_MARKER in output or running_kernel_modules_missing()


# pacman's wording when a package download fails (e.g. a mirror 404).
_RETRIEVE_FAILED = ("failed retrieving file", "failed to retrieve some files")


def rerank_mirrors_command() -> str:
    """CachyOS ships a one-shot mirror ranker; elsewhere suggest reflector."""
    if shutil.which("cachyos-rate-mirrors"):
        return "sudo cachyos-rate-mirrors"
    return "sudo reflector --latest 20 --sort rate --save /etc/pacman.d/mirrorlist"


def failure_hint(output: str) -> str | None:
    """A plain-language explanation for a failed privileged step, based on
    its output - shown in the log and as a popup."""
    if any(marker in output for marker in _RETRIEVE_FAILED):
        return (
            "Some packages couldn't be downloaded because the download mirrors "
            "haven't finished syncing a recent update yet (usually a new release "
            "was published in the last hour or so). Nothing was installed or "
            "changed, and your system is fine.\n\n"
            "Wait 30-60 minutes and press Run again. To try sooner, re-rank your "
            "mirrors in a terminal with:\n\n"
            f"    {rerank_mirrors_command()}\n\n"
            "and then press Run again."
        )
    return None


class InstallRunner(QObject):
    """Runs a queue of AppEntry installs, streaming output.

    Phases:
    1. Unprivileged, per app: for pkgbuild-method apps, fetch source and
       check the PKGBUILD's version against what's already installed - skip
       the (potentially very slow) build entirely if nothing changed.
       pacman-method apps just check `pacman -Q`.
    2. Only if a PKGBUILD still to be built is missing build dependencies
       (a fresh machine, typically): one privileged step installs them, plus
       the run's repo apps - see build_deps_script. Then run makepkg for each.
    3. One single privileged step for the rest of the run: every built package
       gets `pacman -U`'d and every pacman-method app gets `pacman -S`'d in
       ONE `pkexec bash -c "..."` call - exactly like a normal `pacman -Syu`
       only prompts for a password once no matter how many packages it
       touches, instead of once per app.
    """

    log = Signal(str)
    app_started = Signal(str)  # app id
    app_finished = Signal(str, bool)  # app id, success
    failure_explained = Signal(str)  # a failure_hint() explanation
    reboot_recommended = Signal()
    all_finished = Signal()

    def __init__(self, repo_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo_root = repo_root
        self._apps: list[AppEntry] = []
        self._build_queue: list[AppEntry] = []
        self._current_app: AppEntry | None = None
        self._current_commands: list[list[str]] = []
        self._current_cwd: Path | None = None
        self._process: QProcess | None = None
        self._targets: dict[str, str] = {}
        self._resolved: set[str] = set()
        self._pending_pkg_files: list[Path] = []
        self._pending_pacman_installs: list[str] = []
        self._pending_services: list[str] = []
        self._to_build: list[AppEntry] = []
        self._privileged_output: list[str] = []

    def run(self, apps: list[AppEntry]) -> None:
        self._apps = apps
        self._build_queue = list(apps)
        self._targets = {}
        self._resolved = set()
        self._pending_pkg_files = []
        self._pending_pacman_installs = []
        self._to_build = []
        # Checked up front for every selected app, installed or not, so
        # re-running an already-installed app still fixes a disabled service.
        self._pending_services = [
            unit for app in apps for unit in app.services if not service_is_running(unit)
        ]
        self._advance_build_queue()

    def _awaits_services(self, app: AppEntry) -> bool:
        return any(unit in self._pending_services for unit in app.services)

    def _finish_app(self, app_id: str, success: bool) -> None:
        self._resolved.add(app_id)
        self.app_finished.emit(app_id, success)

    def _advance_build_queue(self) -> None:
        if not self._build_queue:
            self._start_build_deps_phase()
            return

        app = self._build_queue.pop(0)
        self._current_app = app
        self.app_started.emit(app.id)

        if app.method == "pacman":
            if is_installed(app):
                if self._awaits_services(app):
                    self.log.emit(f"[{app.id}] already installed, queued service enable")
                else:
                    self.log.emit(f"[{app.id}] already installed, skipping")
                    self._finish_app(app.id, True)
            else:
                self._pending_pacman_installs.append(app.package)
                self.log.emit(f"[{app.id}] queued for install")
            self._advance_build_queue()
            return

        self._current_commands = prepare_commands(app, self._repo_root)
        self._current_cwd = build_dir_for(app)
        self._run_next_prepare_command()

    def _run_next_prepare_command(self) -> None:
        if not self._current_commands:
            self._after_prepare()
            return
        command = self._current_commands.pop(0)
        self._run_command(command, cwd=None, on_finished=self._on_prepare_command_finished)

    def _on_prepare_command_finished(self, command: list[str], exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.log.emit(f"! command failed ({exit_code}): {' '.join(command)}")
            self._finish_app(self._current_app.id, False)
            self._advance_build_queue()
            return
        self._run_next_prepare_command()

    def _after_prepare(self) -> None:
        app = self._current_app
        build_dir = self._current_cwd
        target = pkgbuild_target_version(build_dir)
        current = installed_version(app)
        self._targets[app.id] = target

        if target and current == target:
            self.log.emit(f"[{app.id}] already up to date ({target}), skipping build")
            if not self._awaits_services(app):
                self._finish_app(app.id, True)
            self._advance_build_queue()
            return

        self.log.emit(f"[{app.id}] needs a build (installed: {current or 'none'}, target: {target or 'unknown'})")
        self._to_build.append(app)
        self._advance_build_queue()

    def _start_build_deps_phase(self) -> None:
        missing: list[str] = []
        for app in self._to_build:
            for dep in missing_deps(pkgbuild_build_deps(build_dir_for(app))):
                if dep not in missing:
                    missing.append(dep)
        if not missing:
            self._build_next()
            return

        script = build_deps_script(self._pending_pacman_installs, missing)
        self._pending_pacman_installs = []  # handled by this step now
        self.log.emit("--- privileged step: build dependencies ---")
        self.log.emit(f"(missing to build: {', '.join(missing)})")
        self._run_privileged(script, self._on_build_deps_finished)

    def _on_build_deps_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if not self._privileged_step_ok(exit_code, exit_status):
            # Nothing can build without its deps; the final status check
            # still reports whatever did get installed.
            self._finish_run()
            return
        self._build_next()

    def _build_next(self) -> None:
        if not self._to_build:
            self._start_privileged_phase()
            return
        app = self._to_build.pop(0)
        self._current_app = app
        self.log.emit(f"[{app.id}] building")
        self._run_command(makepkg_command(), cwd=build_dir_for(app), on_finished=self._on_makepkg_finished)

    def _on_makepkg_finished(self, command: list[str], exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        app = self._current_app
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.log.emit(f"! command failed ({exit_code}): {' '.join(command)}")
            self._finish_app(app.id, False)
            self._build_next()
            return

        pkg_path = find_built_package(build_dir_for(app))
        if pkg_path is None:
            self.log.emit(f"! makepkg reported success but no package found for {app.id}")
            self._finish_app(app.id, False)
            self._build_next()
            return

        self._pending_pkg_files.append(pkg_path)
        self.log.emit(f"[{app.id}] built {pkg_path.name}, queued for install")
        self._build_next()

    def _run_command(self, command: list[str], cwd: Path | None, on_finished) -> None:
        self.log.emit(f"$ {' '.join(command)}")
        process = QProcess(self)
        if cwd is not None:
            process.setWorkingDirectory(str(cwd))
        process.readyReadStandardOutput.connect(
            lambda: self.log.emit(bytes(process.readAllStandardOutput()).decode(errors="replace").rstrip())
        )
        process.readyReadStandardError.connect(
            lambda: self.log.emit(bytes(process.readAllStandardError()).decode(errors="replace").rstrip())
        )
        process.finished.connect(lambda code, status: on_finished(command, code, status))
        self._process = process
        process.start(command[0], command[1:])

    def _start_privileged_phase(self) -> None:
        if not (self._pending_pkg_files or self._pending_pacman_installs or self._pending_services):
            self._finish_run()
            return

        script = privileged_script(self._pending_pkg_files, self._pending_pacman_installs, self._pending_services)
        self.log.emit("--- one privileged step for this run ---")
        if self._pending_pacman_installs:
            self.log.emit("(repo installs refresh the package database, so this also upgrades the system)")
        self._run_privileged(script, self._on_privileged_finished)

    def _run_privileged(self, script: str, on_finished) -> None:
        self._privileged_output = []
        self.log.emit(f"$ pkexec bash -c {script!r}")
        process = QProcess(self)
        process.readyReadStandardOutput.connect(
            lambda: self._log_privileged(bytes(process.readAllStandardOutput()))
        )
        process.readyReadStandardError.connect(
            lambda: self._log_privileged(bytes(process.readAllStandardError()))
        )
        process.finished.connect(on_finished)
        self._process = process
        process.start("pkexec", ["bash", "-c", script])

    def _log_privileged(self, data: bytes) -> None:
        text = data.decode(errors="replace").rstrip()
        self._privileged_output.append(text)
        self.log.emit(text)

    def _on_privileged_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._privileged_step_ok(exit_code, exit_status)
        self._finish_run()

    def _privileged_step_ok(self, exit_code: int, exit_status: QProcess.ExitStatus) -> bool:
        """Logs the outcome, surfaces any failure hint / reboot notice."""
        ok = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        self.log.emit("privileged step " + ("succeeded" if ok else f"failed ({exit_code})"))
        if not ok:
            hint = failure_hint("\n".join(self._privileged_output))
            if hint:
                self.log.emit(f"hint: {hint}")
                self.failure_explained.emit(hint)
        # Checked even on failure: `-Syu` may have upgraded the kernel before
        # a later `pacman -U` or `systemctl` step failed.
        if reboot_needed("\n".join(self._privileged_output)):
            self.log.emit("reboot recommended: the kernel or core system packages were upgraded")
            self.reboot_recommended.emit()
        return ok

    def _final_status(self, app: AppEntry) -> bool:
        if not all(service_is_running(unit) for unit in app.services):
            return False
        if app.method == "pacman":
            return is_installed(app)
        target = self._targets.get(app.id)
        version = installed_version(app)
        if target:
            return version == target
        return version is not None

    def _finish_run(self) -> None:
        for app in self._apps:
            if app.id in self._resolved:
                continue
            self._finish_app(app.id, self._final_status(app))
        self.all_finished.emit()
