from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str], base_dir: str | None = None) -> int:
    # base_dir is the source checkout root when run uninstalled (so apps.yaml
    # and pkgbuilds/ resolve next to the code); installed, apps.yaml lives
    # under /usr/share/setup-tool instead.
    if base_dir is not None:
        repo_root = Path(base_dir)
    else:
        repo_root = Path("/usr/share/setup-tool")

    app = QApplication(argv)
    # "apper" only exists in KDE icon themes; fall back to the freedesktop name.
    app.setWindowIcon(QIcon.fromTheme("apper", QIcon.fromTheme("system-software-install")))
    window = MainWindow(repo_root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
