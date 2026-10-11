from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, QSettings, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QTabWidget,
    QTextBrowser,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import selfupdate
from .details import PLACEHOLDER, details_html, repo_infos
from .installer import InstallRunner, installed_version, installed_versions, source_label, sync_repos
from .manifest import AppEntry, load_apps, resolve_fallback
from .selection import dump_selection, parse_selection, plan_load

COLUMN_APP, COLUMN_DESCRIPTION, COLUMN_SOURCE, COLUMN_STATUS = range(4)
COLUMN_HEADERS = ["App", "Description", "Source", "Status"]

# Which side panel is open next to the table; remembered across launches.
PANEL_NONE, PANEL_LOG, PANEL_DETAILS = "none", "log", "details"
SELECTION_FILTER = "Setup Tool selection (*.yaml *.yml);;All files (*)"

STATUS_TEXT = {
    "installed": "Installed",
    "not_installed": "Not installed",
    "running": "Running…",
    "done": "Installed",
    "failed": "Failed",
    "unavailable": "Unavailable",
}


class TallTabBar(QTabBar):
    """Taller tabs, still drawn natively by the style (a stylesheet would
    replace Breeze's rendering and lose its accent line)."""

    EXTRA_HEIGHT = 17

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.setHeight(size.height() + self.EXTRA_HEIGHT)
        return size


class RowHoverDelegate(QStyledItemDelegate):
    """Hover-highlights the whole row under the mouse rather than just the
    one cell (Qt's item views decide hover per cell)."""

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        self._row = -1
        view.setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Leave):
            row = self._view.rowAt(event.position().toPoint().y()) if event.type() == QEvent.Type.MouseMove else -1
            if row != self._row:
                self._row = row
                self._view.viewport().update()
        return False

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if index.row() == self._row:
            option.state |= QStyle.StateFlag.State_MouseOver
        else:
            option.state &= ~QStyle.StateFlag.State_MouseOver


class MainWindow(QMainWindow):
    def __init__(self, repo_root: Path) -> None:
        super().__init__()
        self.setWindowTitle("Setup Tool")
        self._settings = QSettings("setup-tool", "setup-tool")
        saved_geometry = self._settings.value("window/geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)
        else:
            self.resize(1280, 800)

        self._repo_root = repo_root
        apps = load_apps(repo_root / "apps.yaml")
        self._repos = sync_repos([app.package for app in apps if app.method == "pacman"])
        self._apps: list[AppEntry] = [resolve_fallback(app, self._repos) for app in apps]
        # Details-panel data, cached so clicking a row never waits on pacman.
        # Repo info (~0.3s) loads in the background while the window opens.
        pool = ThreadPoolExecutor(max_workers=1)
        repo_packages = [a.package for a in self._apps if a.method == "pacman" and a.package in self._repos]
        self._repo_infos = pool.submit(repo_infos, repo_packages)
        pool.shutdown(wait=False)  # (no `with`: that would wait for the result)
        self._installed: dict[str, str | None] = installed_versions(self._apps)
        self._rows: dict[str, int] = {}
        self._row_apps: dict[int, AppEntry] = {}
        self._run_failure: str | None = None
        self._run_needs_reboot = False
        self._run_apps: list[AppEntry] = []
        self._run_succeeded: set[str] = set()

        self._runner = InstallRunner(repo_root, self)
        self._runner.log.connect(self._append_log)
        self._runner.app_started.connect(lambda app_id: self._set_status(app_id, "running"))
        self._runner.app_finished.connect(self._on_app_finished)
        self._runner.failure_explained.connect(self._on_failure_explained)
        self._runner.reboot_recommended.connect(self._on_reboot_recommended)
        self._runner.all_finished.connect(self._on_run_finished)

        # Left: toolbar + table. Right: the tabbed side panel, full height.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 8)
        toolbar.setSpacing(8)
        select_all_btn = QPushButton("Select All")
        select_none_btn = QPushButton("Select None")
        self._run_btn = QPushButton("Run Selected")
        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        save_btn = QPushButton(QIcon.fromTheme("document-save"), "Save…")
        load_btn = QPushButton(QIcon.fromTheme("document-open"), "Load…")
        save_btn.setToolTip("Save which apps are ticked to a file")
        load_btn.setToolTip("Tick the apps saved in a file")
        save_btn.clicked.connect(self._save_selection)
        load_btn.clicked.connect(self._load_selection)
        self._run_btn.clicked.connect(self._on_run_clicked)
        self._update_btn = QPushButton(QIcon.fromTheme("system-software-update"), "Update Setup Tool")
        self._update_btn.setToolTip("Check for a newer Setup Tool and install it")
        self._update_btn.clicked.connect(lambda: self._check_for_update(manual=True))
        self._toggle_panel_btn = QPushButton()
        self._toggle_panel_btn.setIcon(QIcon.fromTheme("sidebar-expand-right-symbolic"))
        self._toggle_panel_btn.setCheckable(True)
        self._toggle_panel_btn.setToolTip("Show/hide the details and output panel")
        toolbar.addWidget(select_all_btn)
        toolbar.addWidget(select_none_btn)
        toolbar.addWidget(save_btn)
        toolbar.addWidget(load_btn)
        toolbar.addWidget(self._update_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._run_btn)
        toolbar.addWidget(self._toggle_panel_btn)
        layout.addLayout(toolbar)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        # Shown when the startup check finds a newer Setup Tool.
        self._update_banner = QFrame()
        self._update_banner.setStyleSheet("QFrame { background: palette(alternate-base); }")
        banner_layout = QHBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(12, 6, 8, 6)
        banner_icon = QLabel()
        banner_icon.setPixmap(QIcon.fromTheme("system-software-update").pixmap(16, 16))
        banner_layout.addWidget(banner_icon)
        banner_layout.addWidget(QLabel("A newer Setup Tool is available."))
        banner_layout.addStretch()
        self._update_now_btn = QPushButton("Update now")
        self._update_now_btn.clicked.connect(self._start_update)
        banner_layout.addWidget(self._update_now_btn)
        self._update_banner.hide()
        layout.addWidget(self._update_banner)
        self._update_chain: selfupdate.CommandChain | None = None

        table = self._build_table()
        table.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(table)
        splitter.addWidget(left)

        self._details = QTextBrowser()
        self._details.setFrameShape(QFrame.Shape.NoFrame)
        self._details.setOpenExternalLinks(True)
        self._details.document().setDocumentMargin(12)
        self._details.setHtml(PLACEHOLDER)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        self._log.setFrameShape(QFrame.Shape.NoFrame)
        self._log.setStyleSheet("font-family: monospace;")

        # Full-width tabs, like System Settings' pages.
        self._tabs = QTabWidget()
        self._tabs.setTabBar(TallTabBar())
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        self._tabs.addTab(self._details, "Details")
        self._tabs.addTab(self._log, "Output")
        splitter.addWidget(self._tabs)
        splitter.setCollapsible(0, False)
        splitter.setSizes([800, 480])

        self._tabs.currentChanged.connect(lambda _: self._save_panel())
        self._toggle_panel_btn.toggled.connect(self._tabs.setVisible)
        self._toggle_panel_btn.toggled.connect(lambda _: self._save_panel())
        self._set_panel(self._settings.value("window/panel", PANEL_DETAILS))
        self._check_for_update()

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMN_HEADERS))
        table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Selecting a row shows that app in the details panel.
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.currentCellChanged.connect(lambda row, *_: self._show_details(row))
        table.setItemDelegate(RowHoverDelegate(table))
        table.cellDoubleClicked.connect(lambda row, _: self._toggle_checked(row))
        header = table.horizontalHeader()
        header.setHighlightSections(False)  # row selection would light up every header
        header.setSectionResizeMode(COLUMN_APP, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_SOURCE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_STATUS, QHeaderView.ResizeMode.Fixed)
        widest_status = max(STATUS_TEXT.values(), key=len)
        table.setColumnWidth(COLUMN_STATUS, table.fontMetrics().horizontalAdvance(widest_status) + 24)

        self._table = table

        # Right-click the header to hide/show columns when space is tight.
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_menu)
        hidden = self._settings.value("table/hidden_columns", [], type=list)
        for column, title in enumerate(COLUMN_HEADERS):
            if column != COLUMN_APP and title in hidden:
                table.setColumnHidden(column, True)

        categories: dict[str, list[AppEntry]] = {}
        for app in self._apps:
            categories.setdefault(app.category, []).append(app)

        for category, apps in categories.items():
            self._add_category_row(table, category)
            for app in apps:
                self._add_app_row(table, app)

        return table

    def _add_category_row(self, table: QTableWidget, category: str) -> None:
        row = table.rowCount()
        table.insertRow(row)
        item = QTableWidgetItem(category)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        # The theme's window colour (#202326 in Breeze Dark, same as the
        # toolbar) sets category rows apart from the table's darker base.
        item.setBackground(table.palette().window())
        table.setItem(row, 0, item)
        table.setSpan(row, 0, 1, len(COLUMN_HEADERS))

    def _add_app_row(self, table: QTableWidget, app: AppEntry) -> None:
        row = table.rowCount()
        table.insertRow(row)

        name_item = QTableWidgetItem(app.name)
        name_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
        )
        name_item.setCheckState(Qt.CheckState.Unchecked)
        unavailable = app.method == "pacman" and app.package not in self._repos
        if unavailable:
            # Not in any sync repo and no fallback PKGBUILD: selecting it would
            # fail the shared `pacman -Syu` and take every other repo app with it.
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            name_item.setData(Qt.ItemDataRole.CheckStateRole, None)
        table.setItem(row, COLUMN_APP, name_item)

        description_item = QTableWidgetItem(app.description)
        description_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, COLUMN_DESCRIPTION, description_item)

        source_text, source_tooltip = source_label(app, self._repo_root, self._repos)
        source_item = QTableWidgetItem(source_text)
        source_item.setToolTip(source_tooltip)
        source_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, COLUMN_SOURCE, source_item)

        status_item = QTableWidgetItem("")
        status_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        table.setItem(row, COLUMN_STATUS, status_item)

        self._rows[app.id] = row
        self._row_apps[row] = app
        self._set_status(app.id, "installed" if self._installed[app.id] else "not_installed")
        if unavailable and self._installed[app.id] is None:
            self._set_status(app.id, "unavailable")

    def _set_panel(self, panel: str) -> None:
        """Hidden, or open on the Details / Output tab."""
        if panel not in (PANEL_NONE, PANEL_LOG, PANEL_DETAILS):
            panel = PANEL_DETAILS
        # While hidden, still open on the last-used tab when shown again.
        tab = panel if panel != PANEL_NONE else self._settings.value("window/panel_tab", PANEL_DETAILS)
        self._tabs.setCurrentWidget(self._log if tab == PANEL_LOG else self._details)
        self._toggle_panel_btn.setChecked(panel != PANEL_NONE)
        self._tabs.setVisible(panel != PANEL_NONE)

    def _save_panel(self) -> None:
        tab = PANEL_LOG if self._tabs.currentWidget() is self._log else PANEL_DETAILS
        self._settings.setValue("window/panel_tab", tab)
        self._settings.setValue("window/panel", tab if self._toggle_panel_btn.isChecked() else PANEL_NONE)

    def _show_details(self, row: int) -> None:
        app = self._row_apps.get(row)
        if app is None:  # a category header - keep showing the last app
            return
        info = self._repo_infos.result().get(app.package) if app.package else None
        self._details.setHtml(details_html(app, self._repo_root, self._repos, self._installed.get(app.id), info))

    def _show_column_menu(self, pos) -> None:
        header = self._table.horizontalHeader()
        menu = QMenu(self)
        for column, title in enumerate(COLUMN_HEADERS):
            if column == COLUMN_APP:
                continue  # holds the checkboxes, so it always stays
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(not self._table.isColumnHidden(column))
            action.toggled.connect(lambda shown, c=column: self._set_column_shown(c, shown))
        menu.exec(header.mapToGlobal(pos))

    def _set_column_shown(self, column: int, shown: bool) -> None:
        self._table.setColumnHidden(column, not shown)
        hidden = [title for c, title in enumerate(COLUMN_HEADERS) if self._table.isColumnHidden(c)]
        self._settings.setValue("table/hidden_columns", hidden)

    def _refresh_installed_status(self, app: AppEntry) -> None:
        self._installed[app.id] = installed_version(app)
        self._set_status(app.id, "installed" if self._installed[app.id] else "not_installed")

    def _toggle_checked(self, row: int) -> None:
        item = self._table.item(row, COLUMN_APP)
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            checked = item.checkState() == Qt.CheckState.Checked
            item.setCheckState(Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)

    def _checkable_ids(self) -> set[str]:
        return {
            app_id for app_id, row in self._rows.items()
            if self._table.item(row, COLUMN_APP).flags() & Qt.ItemFlag.ItemIsUserCheckable
        }

    def _save_selection(self) -> None:
        start = self._settings.value("selection/dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save selection", str(Path(start) / "setup-tool-selection.yaml"), SELECTION_FILTER
        )
        if not path:
            return
        target = Path(path)
        if target.suffix not in (".yaml", ".yml"):
            target = target.with_name(target.name + ".yaml")
        selected = {app.id for app in self._selected_apps()}
        ids = [app.id for app in self._apps if app.id in self._checkable_ids()]
        text = dump_selection([i for i in ids if i in selected], [i for i in ids if i not in selected])
        try:
            target.write_text(text)
        except OSError as err:
            QMessageBox.warning(self, "Couldn't save selection", str(err))
            return
        self._settings.setValue("selection/dir", str(target.parent))

    def _load_selection(self) -> None:
        start = self._settings.value("selection/dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Load selection", start, SELECTION_FILTER)
        if not path:
            return
        try:
            selected, unselected = parse_selection(Path(path).read_text())
        except (OSError, UnicodeDecodeError, ValueError) as err:
            QMessageBox.warning(self, "Couldn't load selection", str(err))
            return
        self._settings.setValue("selection/dir", str(Path(path).parent))

        checkable = self._checkable_ids()
        plan = plan_load(selected, unselected, [app.id for app in self._apps], checkable)
        for app_id in checkable:
            state = Qt.CheckState.Checked if app_id in plan.check else Qt.CheckState.Unchecked
            self._table.item(self._rows[app_id], COLUMN_APP).setCheckState(state)

        names = {app.id: app.name for app in self._apps}
        notes = []
        if plan.new:
            notes.append("Added since this was saved, so left unticked:\n"
                         + "\n".join(f"  • {names[i]}" for i in plan.new))
        if plan.removed:
            notes.append("No longer in the app list, skipped:\n" + "\n".join(f"  • {i}" for i in plan.removed))
        if plan.unavailable:
            notes.append("Can't be installed on this system, skipped:\n"
                         + "\n".join(f"  • {names[i]}" for i in plan.unavailable))
        if notes:
            box = QMessageBox(self)
            box.setWindowTitle("Selection loaded")
            box.setIcon(QMessageBox.Icon.Information)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(f"Ticked {len(plan.check)} app(s).\n\n" + "\n\n".join(notes))
            box.show()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in self._rows.values():
            item = self._table.item(row, COLUMN_APP)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)

    def _set_status(self, app_id: str, status: str) -> None:
        item = self._table.item(self._rows[app_id], COLUMN_STATUS)
        item.setText(STATUS_TEXT[status])
        color = {
            "installed": "green",
            "done": "green",
            "not_installed": "gray",
            "running": "orange",
            "failed": "red",
            "unavailable": "gray",
        }[status]
        item.setForeground(QColor(color))

    def _append_log(self, text: str) -> None:
        if text:
            self._log.appendPlainText(text)

    def _selected_apps(self) -> list[AppEntry]:
        return [
            app
            for app in self._apps
            if self._table.item(self._rows[app.id], COLUMN_APP).checkState() == Qt.CheckState.Checked
        ]

    def _on_app_finished(self, app_id: str, ok: bool) -> None:
        self._set_status(app_id, "done" if ok else "failed")
        if ok:
            self._run_succeeded.add(app_id)
        row = self._rows[app_id]
        self._installed[app_id] = installed_version(self._row_apps[row])
        if self._table.currentRow() == row:
            self._show_details(row)

    def _on_run_clicked(self) -> None:
        selected = self._selected_apps()
        if not selected:
            return
        self._run_btn.setEnabled(False)
        self._set_update_enabled(False)
        self._run_failure = None
        self._run_needs_reboot = False
        self._run_apps = selected
        self._run_succeeded = set()
        self._set_panel(PANEL_LOG)  # show progress as it happens
        self._log.appendPlainText(f"--- installing {len(selected)} app(s) ---")
        self._runner.run(selected)

    def _on_run_finished(self) -> None:
        self._run_btn.setEnabled(True)
        self._set_update_enabled(self._update_chain is None)
        self._log.appendPlainText("--- done ---")
        if self._run_failure:
            self._show_failure(self._run_failure)
        if self._run_needs_reboot:
            self._show_reboot()
        self._show_next_steps()

    def _show_next_steps(self) -> None:
        """Manual follow-ups (logins etc.) for apps that just installed OK -
        shown in a dialog too, since the log pane is hidden by default."""
        apps = [
            app
            for app in self._run_apps
            if app.id in self._run_succeeded and app.next_steps
        ]
        if not apps:
            return
        text = "\n\n".join(f"{app.name}:\n{app.next_steps.strip()}" for app in apps)
        # Copy grabs just the commands, without the "App:" headers, so the
        # clipboard pastes straight into a terminal.
        commands = "\n".join(app.next_steps.strip() for app in apps)
        self._log.appendPlainText(f"--- next steps ---\n{text}")

        # A QDialog rather than QMessageBox: every QMessageBox button closes
        # the box, and Copy should leave it open.
        dialog = QDialog(self)
        dialog.setWindowTitle("Next steps")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Finish setting these up in a terminal:"))
        steps_view = QPlainTextEdit(text)
        steps_view.setReadOnly(True)
        steps_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        steps_view.setMinimumSize(520, 160)
        layout.addWidget(steps_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy_btn = buttons.addButton("Copy commands", QDialogButtonBox.ButtonRole.ActionRole)
        copy_btn.setIcon(QIcon.fromTheme("edit-copy"))

        def copy() -> None:
            QGuiApplication.clipboard().setText(commands)
            copy_btn.setText("Copied")

        copy_btn.clicked.connect(copy)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.show()

    def _on_reboot_recommended(self) -> None:
        self._run_needs_reboot = True

    def _show_reboot(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Restart recommended")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "This run upgraded the Linux kernel or other core system packages.\n\n"
            "Restart your computer soon. Until you do, some things may not work "
            "properly - for example VPN apps like Mullvad can fail to connect."
        )
        box.show()

    def _on_failure_explained(self, explanation: str) -> None:
        self._run_failure = explanation

    def _show_failure(self, explanation: str) -> None:
        """Explains known, temporary failures (e.g. mirrors mid-sync) so the
        user knows to wait rather than dig into the log."""
        box = QMessageBox(self)
        box.setWindowTitle("Install failed")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(explanation)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.show()

    # --- self-update -------------------------------------------------------

    def _set_update_enabled(self, enabled: bool) -> None:
        self._update_btn.setEnabled(enabled)
        self._update_now_btn.setEnabled(enabled)

    def _check_for_update(self, manual: bool = False) -> None:
        """Runs in the background; the startup check (manual=False) stays
        silent unless there is an update - offline just means no banner."""
        if self._update_chain is not None:
            return
        self._update_btn.setEnabled(False)
        chain = selfupdate.CommandChain(selfupdate.check_commands(self._repo_root), self)
        chain.finished.connect(lambda ok, output: self._on_update_checked(ok, output, manual))
        self._update_chain = chain
        chain.start()

    def _on_update_checked(self, ok: bool, output: str, manual: bool) -> None:
        self._update_chain = None
        self._update_btn.setEnabled(self._run_btn.isEnabled())
        if not ok:
            if manual:
                box = QMessageBox(self)
                box.setWindowTitle("Couldn't check for updates")
                box.setIcon(QMessageBox.Icon.Warning)
                box.setText("Couldn't reach GitHub to check for a newer Setup Tool. Check your internet connection and try again.")
                box.setDetailedText(output.strip() or None)
                box.show()
            return
        if selfupdate.is_source_checkout(self._repo_root):
            available = selfupdate.source_update_available(output)
        else:
            available = selfupdate.packaged_update_available(
                selfupdate.packaged_commit(self._repo_root), selfupdate.parse_ls_remote(output)
            )
        self._update_banner.setVisible(available)
        if not manual:
            return
        if available:
            self._start_update()
        else:
            QMessageBox.information(self, "Setup Tool", "Setup Tool is up to date.")

    def _start_update(self) -> None:
        if self._update_chain is not None or not self._run_btn.isEnabled():
            return
        self._run_btn.setEnabled(False)
        self._set_update_enabled(False)
        self._set_panel(PANEL_LOG)
        self._log.appendPlainText("--- updating Setup Tool ---")
        chain = selfupdate.CommandChain(selfupdate.update_commands(self._repo_root), self)
        chain.output.connect(self._append_log)
        chain.finished.connect(self._on_updated)
        self._update_chain = chain
        chain.start()

    def _on_updated(self, ok: bool, output: str) -> None:
        self._update_chain = None
        if ok:
            self._log.appendPlainText("--- updated, restarting ---")
            # Same interpreter and launcher as this run, so a source checkout
            # restarts from the checkout and an install from /usr/bin.
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0]), *sys.argv[1:]])
            self.close()
            return
        self._log.appendPlainText("--- update failed ---")
        self._run_btn.setEnabled(True)
        self._set_update_enabled(True)
        box = QMessageBox(self)
        box.setWindowTitle("Update failed")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Setup Tool couldn't update itself. Nothing was changed - the Output panel has the details.")
        box.show()

    def closeEvent(self, event) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        super().closeEvent(event)
