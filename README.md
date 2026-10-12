# Setup Tool

A personal app-installer checklist, in the spirit of Chris Titus Tech's WinUtil:
on a fresh machine, check the apps you want and hit **Run Selected**.

## Adding apps

Edit `apps.yaml` — no code changes needed. Three install methods:

- **`method: pacman`** — a plain sync-repo package (`package: <name>`).
  Optionally `fallback: pkgbuilds/<id>`: a bundled PKGBUILD built instead
  when no configured repo has the package (e.g. `[cachyos]`-only packages
  like Zen, Helium and OnlyOffice on plain Arch). Without a fallback, such
  apps show as *Unavailable* and can't be selected.
- **`method: pkgbuild`** — build and install a PKGBUILD you maintain. Either:
  - `source: pkgbuilds/<id>` — a PKGBUILD bundled in this repo (AUR
    replacements you've taken over maintaining), or
  - `git: <url>` — a separately-hosted repo to clone fresh (your own
    projects, e.g. clipcut/file-sifter/mediaconvert). Use the public
    `https://` URL so a fresh machine can clone without SSH keys.
- **`method: script`** — the vendor's own installer, for apps that install
  into your home folder and update themselves (e.g. Claude Code):
  `script: <https url>` is downloaded and run as you, never as root, and
  `creates: <path>` is the file whose existence means it's installed. Only
  use this for installers published by the app's own vendor.

You are the sole maintainer of everything referenced here — there's no
per-run auditing, so only add PKGBUILDs you've personally reviewed.

### Bundled PKGBUILD conventions

Every PKGBUILD here is **our own** — the single source of trust for what
gets built. Nothing is ever fetched from the AUR, and no AUR files are
copied in: an AUR package can be taken over or quietly changed, and a copy
would inherit that. When packaging something new, write it from upstream's
own release (an AUR PKGBUILD is fine to *read* for hints, never to copy),
and derive every checksum from upstream yourself.

- **Header format:** what the app is, then `Source:`
  (upstream URL, built vs repackaged), `Verified:` (signature/hash and
  where the key comes from) and `Updating:` (how to bump it). Helper files
  (launchers, `.desktop` entries, licence notes) are ours too; prefer
  editing upstream's own files in `prepare()` over carrying patches.

So the tool works on any Arch-based machine, not just one that's been set
up by hand:

- **Pin a sha256 for every download, no `SKIP`, no `validpgpkeys`.**
  makepkg only checks signatures against the *building user's* GPG
  keyring, so `validpgpkeys` fails on a fresh machine. When upstream signs
  releases (Sublime, 1Password, Helium), verify the signature by hand when
  bumping — download the file + signature, `gpg --verify` against the key
  from upstream's official URL — then pin the sha256. Each PKGBUILD's
  header comment names the key and where it comes from.
- **Declare every build/runtime dependency** in top-level `depends` /
  `makedepends`: missing ones are installed automatically before building.

## How installs run

1. **Unprivileged prep, per app.** `pacman` apps check `pacman -Q`.
   `pkgbuild` apps are copied/cloned into `~/.cache/setup-tool/build/<id>/`
   and their PKGBUILD version compared to what's installed — unchanged
   ones skip the build entirely.
2. **Build dependencies (only when some are missing).** Any `depends` /
   `makedepends` / `checkdepends` not yet installed for the PKGBUILDs about
   to be built are installed in one `pkexec` step (`--asdeps`), together
   with the run's repo apps. Already-set-up machines never see this step.
3. **Build.** `makepkg -f --noconfirm` runs **unprivileged** for each.
4. **Install.** One `pkexec` step for everything left: repo apps
   (`pacman -Syu --needed`), built packages (`pacman -U`) and services
   (`systemctl enable --now`).

   Apps' `root_commands` (e.g. making you Tailscale's operator) run at
   the end of this same step.
5. **Your setup, unprivileged.** `user_services` are enabled with
   `systemctl --user`, then any `login` whose check fails runs: the sign-in
   link it prints opens in your browser and the run waits (up to 10
   minutes) for you to finish.

Every step checks first and skips what's already done - services running,
operator set, already signed in - so on a machine that's already set up a
run changes nothing and asks for no password. Steps apps share (Tailscale
and Taildrop both need `tailscaled`) run once.

So a run asks for your password once — twice at most, on a fresh machine
that's missing build dependencies. Repo installs always use `-Syu`, never a
partial `-Sy`, so they also upgrade the system.

## Running

```
./setup-tool
```

Or build it like any of the other personal apps here: `makepkg -si`.

## Updating Setup Tool itself

On start, Setup Tool checks GitHub in the background and shows a bar above
the list when a newer version exists; **Update Setup Tool** in the toolbar
checks on demand. Updating clones the repo, runs `makepkg`, installs it with
one password prompt and restarts the app. Run from a source checkout, it
does a `git pull --ff-only` instead.

Since `pkgver` never changes, "newer" means a different commit: the PKGBUILD
writes the commit it was built from to `/usr/share/setup-tool/commit`, which
is compared with GitHub's `HEAD`. A checkout instead counts how many commits
it's behind its upstream, so unpushed local commits never look like updates.

## Updating by hand (after editing setup-tool's own code)

If you just want to try a change, `./setup-tool` runs straight from this
source checkout - no build step needed, since it's plain Python.

But if it's installed via pacman (`pacman -Q setup-tool`), that installed
copy is frozen at whatever was packaged - editing the source here does
**not** affect it until you rebuild and reinstall:

```
makepkg -f
pkexec pacman -U --noconfirm setup-tool-*-any.pkg.tar.zst
```

`makepkg -f` forces a rebuild even though the version number hasn't
changed (bump `pkgver`/`pkgrel` in `PKGBUILD` if you want it to show up as
a real upgrade rather than a same-version reinstall). `pacman -U` needs an
absolute or `./`-relative path since it's run through `pkexec`, which
doesn't share your shell's `cwd` assumptions the way plain `sudo` does.

## Ubuntu, Zorin, Mint and other apt distros

`apt/setup-apt` is a terminal version of the same app list for Ubuntu-based
distros (24.04+). Each app comes from the most official source there: the
vendor's apt repo or .deb, the project's PPA, Ubuntu's repos, then Flathub.
Ghostty, Helium and the My Apps PKGBUILDs aren't covered.

```
curl -fsSLO https://raw.githubusercontent.com/missingfoot/setup-tool/main/apt/setup-apt
chmod +x setup-apt
./setup-apt              # checklist
./setup-apt --list       # app IDs, for ./setup-apt ID...
```

Unlike `apps.yaml`, its list lives in the script itself (`APPS` plus one
`plan_<id>` function per app), so adding an app there means editing both.

## Tests

```
pytest
```
