from pathlib import Path

from setuptoollib.installer import (
    build_dir_for,
    failure_hint,
    reboot_needed,
    running_kernel_modules_missing,
    find_built_package,
    installed_version,
    is_installed,
    pacman_name,
    pkgbuild_target_version,
    prepare_commands,
    privileged_script,
    build_deps_script,
    missing_deps,
    pkgbuild_build_deps,
    rerank_mirrors_command,
    source_label,
    sync_repos,
)
from setuptoollib.manifest import AppEntry

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_prepare_commands_local_source():
    app = AppEntry(
        id="sublime-text-4",
        name="Sublime",
        category="Editors",
        description="",
        method="pkgbuild",
        source="pkgbuilds/sublime-text-4",
    )
    commands = prepare_commands(app, REPO_ROOT)
    build_dir = str(build_dir_for(app))
    assert commands[0] == ["rm", "-rf", build_dir]
    assert commands[1] == ["mkdir", "-p", build_dir]
    assert commands[2][0] == "cp"
    assert commands[2][1] == "-r"
    assert commands[2][2].endswith("pkgbuilds/sublime-text-4/.")
    assert commands[2][3] == build_dir


def test_prepare_commands_git_source():
    app = AppEntry(
        id="clipcut",
        name="ClipCut",
        category="My Apps",
        description="",
        method="pkgbuild",
        git="https://example.com/clipcut.git",
    )
    commands = prepare_commands(app, REPO_ROOT)
    build_dir = str(build_dir_for(app))
    assert commands[0] == ["rm", "-rf", build_dir]
    assert commands[1] == ["git", "clone", "--depth", "1", "https://example.com/clipcut.git", build_dir]


def test_find_built_package(tmp_path):
    assert find_built_package(tmp_path) is None
    pkg = tmp_path / "foo-1.0-1-x86_64.pkg.tar.zst"
    pkg.write_bytes(b"")
    assert find_built_package(tmp_path) == pkg


def test_pacman_name_prefers_package_field():
    pacman_app = AppEntry(id="fzf", name="fzf", category="CLI", description="", method="pacman", package="fzf")
    assert pacman_name(pacman_app) == "fzf"

    pkgbuild_app = AppEntry(
        id="sublime-text-4",
        name="Sublime",
        category="Editors",
        description="",
        method="pkgbuild",
        source="pkgbuilds/sublime-text-4",
    )
    assert pacman_name(pkgbuild_app) == "sublime-text-4"


def test_pkgbuild_target_version_static(tmp_path):
    (tmp_path / "PKGBUILD").write_text("pkgname=foo\npkgver=1.2.3\npkgrel=4\n")
    assert pkgbuild_target_version(tmp_path) == "1.2.3-4"


def test_pkgbuild_target_version_with_epoch(tmp_path):
    (tmp_path / "PKGBUILD").write_text("pkgname=foo\nepoch=1\npkgver=3.23.3\npkgrel=1\n")
    assert pkgbuild_target_version(tmp_path) == "1:3.23.3-1"


def test_pkgbuild_target_version_with_variable_indirection(tmp_path):
    # mirrors the real 1password PKGBUILD: pkgver derived from another var
    (tmp_path / "PKGBUILD").write_text('_tarver=8.12.36\npkgver=${_tarver//-/_}\npkgrel=42\n')
    assert pkgbuild_target_version(tmp_path) == "8.12.36-42"


def test_installed_version_known_package():
    # bash is always installed on Arch - a real, stable positive case
    assert installed_version(AppEntry(id="bash", name="bash", category="C", description="", method="pacman", package="bash")) is not None


def test_is_installed_false_for_bogus_package():
    app = AppEntry(id="x", name="x", category="C", description="", method="pacman", package="definitely-not-a-real-package-xyz")
    assert is_installed(app) is False


def test_privileged_script_repo_installs_refresh_and_upgrade():
    assert privileged_script([], ["telegram-desktop", "vlc"]) == "pacman -Syu --needed --noconfirm telegram-desktop vlc"


def test_privileged_script_upgrades_before_local_packages():
    script = privileged_script([Path("/b/foo-1-1-x86_64.pkg.tar.zst")], ["vlc"])
    assert script == "pacman -Syu --needed --noconfirm vlc && pacman -U --noconfirm /b/foo-1-1-x86_64.pkg.tar.zst"


def test_privileged_script_local_packages_only_skips_refresh():
    assert privileged_script([Path("/b/foo.pkg.tar.zst")], []) == "pacman -U --noconfirm /b/foo.pkg.tar.zst"


def test_failure_hint_on_download_failure():
    output = (
        "error: failed retrieving file 'telegram-desktop-7.2.8-1.1-x86_64_v3.pkg.tar.zst.sig' "
        "from mirror.krfoss.org : The requested URL returned error: 404\n"
        "warning: failed to retrieve some files"
    )
    assert rerank_mirrors_command() in failure_hint(output)


def test_failure_hint_none_for_other_errors():
    assert failure_hint("error: target not found: nope") is None


def test_privileged_script_enables_services_last():
    script = privileged_script([Path("/b/foo.pkg.tar.zst")], ["tailscale"], ["tailscaled.service"])
    assert script.endswith(" && systemctl enable --now tailscaled.service")
    assert script.startswith("pacman -Syu")


def test_privileged_script_services_only():
    # re-running an already-installed app just fixes its service
    assert privileged_script([], [], ["mullvad-daemon.service"]) == "systemctl enable --now mullvad-daemon.service"


def test_running_kernel_modules_missing(tmp_path):
    import platform
    assert running_kernel_modules_missing(tmp_path) is True
    (tmp_path / platform.release()).mkdir()
    assert running_kernel_modules_missing(tmp_path) is False


def test_reboot_needed_from_hook_output():
    assert reboot_needed("==> INFO: Reboot is recommended due to the upgrade of core system package(s).")


def test_sync_repos_known_package():
    assert "pacman" in sync_repos(["pacman"])


def test_sync_repos_skips_unknown_but_keeps_others():
    repos = sync_repos(["pacman", "definitely-not-a-real-package-xyz"])
    assert "pacman" in repos
    assert "definitely-not-a-real-package-xyz" not in repos


def test_source_label_pacman():
    app = AppEntry(id="x", name="X", category="C", description="", method="pacman", package="x")
    assert source_label(app, REPO_ROOT, {"x": "extra"})[0] == "extra"
    assert source_label(app, REPO_ROOT, {})[0] == "pacman"


def test_source_label_bundled_pkgbuild(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "PKGBUILD").write_text('url="https://www.example.com/app"\n')
    app = AppEntry(id="x", name="X", category="C", description="", method="pkgbuild", source="pkg")
    text, tooltip = source_label(app, tmp_path, {})
    assert text == "PKGBUILD · example.com"
    assert "https://www.example.com/app" in tooltip


def test_source_label_git():
    app = AppEntry(id="x", name="X", category="C", description="", method="pkgbuild", git="https://g/x.git")
    assert source_label(app, REPO_ROOT, {})[0] == "Git PKGBUILD"


def test_rerank_mirrors_command_matches_distro(monkeypatch):
    import setuptoollib.installer as inst
    monkeypatch.setattr(inst.shutil, "which", lambda name: "/usr/bin/" + name)
    assert rerank_mirrors_command() == "sudo cachyos-rate-mirrors"
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    assert "reflector" in rerank_mirrors_command()


def test_pkgbuild_build_deps_includes_all_kinds_and_arch_specific(tmp_path):
    import platform
    arch = platform.machine()
    (tmp_path / "PKGBUILD").write_text(
        "depends=(a 'b>=2')\n"
        "makedepends=(c)\n"
        "checkdepends=(d)\n"
        f"depends_{arch}=(e)\n"
        "depends_notarealarch=(z)\n"
        "package() { depends=(inside); }\n"
    )
    assert sorted(pkgbuild_build_deps(tmp_path)) == ["a", "b>=2", "c", "d", "e"]


def test_missing_deps():
    assert missing_deps([]) == []
    assert missing_deps(["pacman", "definitely-not-a-real-package-xyz"]) == ["definitely-not-a-real-package-xyz"]


def test_build_deps_script():
    assert build_deps_script([], ["meson"]) == (
        "pacman -Syu --needed --noconfirm && pacman -S --needed --asdeps --noconfirm meson"
    )
    assert build_deps_script(["vlc"], ["qt6-base>=6"]) == (
        "pacman -Syu --needed --noconfirm vlc && pacman -S --needed --asdeps --noconfirm 'qt6-base>=6'"
    )
