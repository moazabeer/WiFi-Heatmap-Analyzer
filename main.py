"""
main.py — WiFi3D Mapper
Entry point.  Builds the main window with:
  • Toolbar (Start / Stop / Export / Clear / Screenshot)
  • Network table (live, sortable)
  • 3-D heatmap (OpenGL)
  • Live RSSI graph
  • Status bar with scan counter
"""

import sys
import os
import math
import random
import datetime
from typing import List, Dict, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QFileDialog, QStatusBar, QToolBar,
    QFrame, QSizePolicy, QMessageBox, QSpacerItem,
)
from PyQt6.QtCore    import Qt, QTimer, QSize, pyqtSlot
from PyQt6.QtGui     import (
    QColor, QFont, QIcon, QPixmap, QPainter,
    QLinearGradient, QBrush, QPen,
)

from database      import DatabaseManager
from wifi_scanner  import WiFiScanner
from visualization import HeatmapWidget, RSSIGraphWidget
from heatmap       import ROOM_WIDTH, ROOM_DEPTH


# ============================================================================
# Dark theme  (applied as a global QSS stylesheet)
# ============================================================================

DARK_QSS = """
QWidget {
    background-color: #0D1117;
    color: #B0C4D8;
    font-family: "Segoe UI", "SF Pro Display", "Ubuntu", sans-serif;
    font-size: 13px;
}
QMainWindow::separator { background: #1C2333; width: 2px; height: 2px; }
QSplitter::handle      { background: #1C2333; }
QToolBar {
    background: #161B27;
    border-bottom: 1px solid #2A3446;
    spacing: 6px;
    padding: 4px 8px;
}
QPushButton {
    background: #1E2D40;
    color: #9BBFD8;
    border: 1px solid #2A3A52;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 600;
}
QPushButton:hover   { background: #243650; color: #C8DFF0; border-color: #3A5070; }
QPushButton:pressed { background: #1A2636; }
QPushButton:disabled{ color: #3A4A5A; border-color: #1E2A38; background: #141B26; }
QPushButton#btn_start {
    background: #0D3040;
    color: #00C8FF;
    border-color: #0A7090;
}
QPushButton#btn_start:hover { background: #0E3D50; }
QPushButton#btn_stop {
    background: #3D1020;
    color: #FF6080;
    border-color: #801030;
}
QPushButton#btn_stop:hover { background: #4D1428; }
QTableWidget {
    background: #0D1117;
    alternate-background-color: #111820;
    gridline-color: #1A2230;
    border: none;
    selection-background-color: #1A3050;
    selection-color: #C8E0FF;
}
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section {
    background: #141C28;
    color: #7090A8;
    border: none;
    border-bottom: 1px solid #1E2A3A;
    padding: 6px 8px;
    font-weight: 700;
    letter-spacing: 0.05em;
    font-size: 11px;
    text-transform: uppercase;
}
QScrollBar:vertical {
    background: #0D1117; width: 8px; border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #2A3A52; border-radius: 4px; min-height: 20px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QStatusBar {
    background: #10161F;
    color: #506070;
    border-top: 1px solid #1A2230;
    font-size: 11px;
}
QLabel#section_title {
    color: #4A6080;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    padding: 4px 8px 2px 8px;
}
QFrame#divider {
    background: #1A2230;
    max-height: 1px;
}
"""


# ============================================================================
# Signal-strength → coloured text helper
# ============================================================================

def _rssi_color(rssi: int) -> str:
    if rssi >= -50:  return "#00E8A0"   # excellent — green
    if rssi >= -65:  return "#80D060"   # good      — lime
    if rssi >= -75:  return "#F0C040"   # fair      — amber
    if rssi >= -85:  return "#F06030"   # weak      — orange
    return "#C03050"                     # poor      — red


def _signal_bar(rssi: int) -> str:
    """Return a compact Unicode bar indicator."""
    if rssi >= -50:  return "▰▰▰▰▰"
    if rssi >= -65:  return "▰▰▰▰▱"
    if rssi >= -75:  return "▰▰▰▱▱"
    if rssi >= -85:  return "▰▰▱▱▱"
    return "▰▱▱▱▱"


# ============================================================================
# Custom table item that sorts numerically for integer columns
# ============================================================================

class _NumericItem(QTableWidgetItem):
    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            return float(self.data(Qt.ItemDataRole.UserRole)) < \
                   float(other.data(Qt.ItemDataRole.UserRole))
        except Exception:
            return super().__lt__(other)


# ============================================================================
# Main window
# ============================================================================

class MainWindow(QMainWindow):
    """
    Top-level application window for WiFi3D Mapper.

    Layout
    ------
    ┌──────────────── Toolbar ─────────────────────┐
    │  ┌─── Left panel ───┐  ┌─── Right panel ───┐ │
    │  │  Network Table   │  │   3-D Heatmap     │ │
    │  │                  │  │                   │ │
    │  │  ─── divider ─── │  │  ─── divider ─── │ │
    │  │  RSSI Live Graph │  │   (status info)   │ │
    │  └──────────────────┘  └───────────────────┘ │
    └──────────────────────────────────────────────┘
    """

    # How often (ms) the UI table and graph are refreshed from last scan data
    UI_REFRESH_INTERVAL = 500

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WiFi3D Mapper")
        self.resize(1440, 860)

        # ── Core objects ───────────────────────────────────────────────
        self._db      = DatabaseManager("wifi_scans.db")
        self._scanner = WiFiScanner()
        self._latest_networks: List[Dict] = []
        self._scan_count = 0

        # Virtual "room" positions cycle through a grid so every scan
        # gets a unique XY coordinate (simulating the user walking around).
        self._pos_index = 0

        # ── Build UI ──────────────────────────────────────────────────
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # ── Connect scanner signals ───────────────────────────────────
        self._scanner.networks_found.connect(self._on_networks_found)
        self._scanner.scan_error.connect(self._on_scan_error)

        # ── Periodic UI refresh timer ─────────────────────────────────
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(self.UI_REFRESH_INTERVAL)
        self._ui_timer.timeout.connect(self._refresh_ui)

        self.setStyleSheet(DARK_QSS)
        self._set_scanning(False)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main toolbar", self)
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        # ── Logo label ─────────────────────────────────────────────────
        logo = QLabel(" 📡  WiFi3D Mapper")
        logo.setStyleSheet(
            "color:#00B4E0; font-size:16px; font-weight:800; "
            "letter-spacing:0.04em; padding-right:20px;"
        )
        tb.addWidget(logo)
        tb.addSeparator()

        # ── Buttons ────────────────────────────────────────────────────
        self.btn_start = QPushButton("▶  Start Scan")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setFixedHeight(34)
        self.btn_start.clicked.connect(self._start_scan)
        tb.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■  Stop")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFixedHeight(34)
        self.btn_stop.clicked.connect(self._stop_scan)
        tb.addWidget(self.btn_stop)

        tb.addSeparator()

        self.btn_export = QPushButton("⬇  Export CSV")
        self.btn_export.setFixedHeight(34)
        self.btn_export.clicked.connect(self._export_csv)
        tb.addWidget(self.btn_export)

        self.btn_screenshot = QPushButton("📷  Screenshot")
        self.btn_screenshot.setFixedHeight(34)
        self.btn_screenshot.clicked.connect(self._save_screenshot)
        tb.addWidget(self.btn_screenshot)

        tb.addSeparator()

        self.btn_clear = QPushButton("🗑  Clear DB")
        self.btn_clear.setFixedHeight(34)
        self.btn_clear.clicked.connect(self._clear_database)
        tb.addWidget(self.btn_clear)

        # ── Right spacer + info label ──────────────────────────────────
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self.lbl_scan_info = QLabel("Idle")
        self.lbl_scan_info.setStyleSheet("color:#3A5060; font-size:12px; padding-right:12px;")
        tb.addWidget(self.lbl_scan_info)

    # ------------------------------------------------------------------
    # Central widget
    # ------------------------------------------------------------------

    def _build_central(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)
        root_layout.addWidget(splitter)

        # ── Left panel ─────────────────────────────────────────────────
        left = QSplitter(Qt.Orientation.Vertical)
        left.setHandleWidth(4)

        top_left = QWidget()
        tlv = QVBoxLayout(top_left)
        tlv.setContentsMargins(0, 0, 0, 0)
        tlv.setSpacing(0)
        tlv.addWidget(self._section_label("Live Networks"))
        self._build_table(tlv)

        bot_left = QWidget()
        blv = QVBoxLayout(bot_left)
        blv.setContentsMargins(0, 0, 0, 0)
        blv.setSpacing(0)
        blv.addWidget(self._section_label("RSSI History"))
        self.rssi_graph = RSSIGraphWidget()
        blv.addWidget(self.rssi_graph)

        left.addWidget(top_left)
        left.addWidget(bot_left)
        left.setSizes([420, 280])

        # ── Right panel ────────────────────────────────────────────────
        right = QWidget()
        rv    = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        rv.addWidget(self._section_label("3-D Signal Heatmap"))
        self.heatmap = HeatmapWidget()
        rv.addWidget(self.heatmap, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([420, 980])

    def _build_table(self, parent_layout: QVBoxLayout) -> None:
        """Create the live network table."""
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["SSID", "BSSID", "Signal", "Bars", "Channel", "Security"]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setRowHeight(0, 30)
        parent_layout.addWidget(self.table, stretch=2)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_status = QLabel("Ready")
        sb.addPermanentWidget(self.lbl_status, 1)

        self.lbl_db_count = QLabel("DB: 0 records")
        sb.addPermanentWidget(self.lbl_db_count)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_title")
        return lbl

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("divider")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        if self._scanner.isRunning():
            return
        self._scanner.start()
        self._ui_timer.start()
        self._set_scanning(True)
        self._update_status("Scanning…")

    def _stop_scan(self) -> None:
        if not self._scanner.isRunning():
            return
        self._scanner.stop()
        self._ui_timer.stop()
        self._set_scanning(False)
        self._update_status("Scan stopped.")

    def _set_scanning(self, active: bool) -> None:
        self.btn_start.setEnabled(not active)
        self.btn_stop.setEnabled(active)
        if active:
            self.lbl_scan_info.setText("● Scanning every 2 s")
            self.lbl_scan_info.setStyleSheet("color:#00B4E0; font-size:12px; padding-right:12px;")
        else:
            self.lbl_scan_info.setText("Idle")
            self.lbl_scan_info.setStyleSheet("color:#3A5060; font-size:12px; padding-right:12px;")

    # ------------------------------------------------------------------
    # Scanner callbacks
    # ------------------------------------------------------------------

    @pyqtSlot(list)
    def _on_networks_found(self, networks: List[Dict]) -> None:
        """
        Called on every scan completion.
        Assigns virtual XY positions and persists to database.
        """
        self._scan_count += 1
        self._latest_networks = networks

        if not networks:
            return

        # Assign virtual room coordinates in a spiral grid so that
        # successive scans map to different XY positions on the heatmap.
        x, y = self._next_position()
        print(f"Scan {self._scan_count}: x={x:.2f}, y={y:.2f}")

        tagged = []
        for net in networks:
            net_copy = dict(net)
            net_copy["x_pos"] = x
            net_copy["y_pos"] = y
            tagged.append(net_copy)

        # Persist batch
        self._db.insert_batch(tagged)

    @pyqtSlot(str)
    def _on_scan_error(self, msg: str) -> None:
        self._update_status(f"⚠  {msg}")

    # ------------------------------------------------------------------
    # UI refresh (timer-driven, ~500 ms)
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _refresh_ui(self) -> None:
        """Update table, graph, and 3-D view from latest data."""
        networks = self._latest_networks
        self._populate_table(networks)
        self.rssi_graph.update_networks(networks)

        # Pull recent heatmap data from DB (last 300 rows)
        samples = self._db.get_recent_scans(2000)
        self.heatmap.update_heatmap(samples)

        # Status bar
        count = self._db.count_scans()
        self.lbl_db_count.setText(f"DB: {count:,} records")
        self._update_status(
            f"Scan #{self._scan_count}  ·  "
            f"{len(networks)} network{'s' if len(networks) != 1 else ''} visible"
        )

    def _populate_table(self, networks: List[Dict]) -> None:
        """Rebuild the live network table rows."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for net in networks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setRowHeight(row, 28)

            rssi = net["signal_strength"]
            col  = _rssi_color(rssi)

            # SSID
            self.table.setItem(row, 0, QTableWidgetItem(net["ssid"]))

            # BSSID
            bssid_item = QTableWidgetItem(net["bssid"])
            bssid_item.setForeground(QColor(60, 90, 110))
            self.table.setItem(row, 1, bssid_item)

            # Signal (numeric sort)
            sig_item = _NumericItem(f"{rssi} dBm")
            sig_item.setData(Qt.ItemDataRole.UserRole, rssi)
            sig_item.setForeground(QColor(col))
            sig_item.setFont(QFont("Consolas", 11))
            self.table.setItem(row, 2, sig_item)

            # Bars
            bar_item = QTableWidgetItem(_signal_bar(rssi))
            bar_item.setForeground(QColor(col))
            bar_item.setFont(QFont("Consolas", 11))
            self.table.setItem(row, 3, bar_item)

            # Channel
            ch_item = _NumericItem(str(net["channel"]))
            ch_item.setData(Qt.ItemDataRole.UserRole, net["channel"])
            ch_item.setForeground(QColor(80, 110, 130))
            self.table.setItem(row, 4, ch_item)

            # Security
            sec_item = QTableWidgetItem(net.get("security", ""))
            sec_item.setForeground(QColor(80, 130, 90) if "WPA" in net.get("security","") else QColor(130, 80, 80))
            self.table.setItem(row, 5, sec_item)

        self.table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Position generator  (spiral grid)
    # ------------------------------------------------------------------

    def _next_position(self) -> tuple:
        """Generate positions distributed across the whole room using
        a sunflower/golden-angle spiral pattern."""
 
        idx = self._pos_index
        self._pos_index += 1

        max_points = 500
        golden_angle = 2.399963229728653  # radians

        radius = min(ROOM_WIDTH, ROOM_DEPTH) * 0.45 * math.sqrt(idx / max_points)

        angle = idx * golden_angle

        x = ROOM_WIDTH / 2 + radius * math.cos(angle)
        y = ROOM_DEPTH / 2 + radius * math.sin(angle)

        x = max(0.5, min(ROOM_WIDTH - 0.5, x))
        y = max(0.5, min(ROOM_DEPTH - 0.5, y))

        return x, y

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV",
            f"wifi_scan_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            n = self._db.export_csv(path)
            QMessageBox.information(self, "Export Complete", f"Exported {n:,} rows to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _save_screenshot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Screenshot",
            f"heatmap_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            "PNG Images (*.png)",
        )
        if not path:
            return
        try:
            self.heatmap.save_screenshot(path)
            QMessageBox.information(self, "Screenshot Saved", f"Saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Screenshot Failed", str(exc))

    def _clear_database(self) -> None:
        reply = QMessageBox.question(
            self, "Clear Database",
            "Delete ALL scan data from the database?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_all()
            self._db.vacuum()
            self.rssi_graph.clear()
            self._latest_networks = []
            self._scan_count = 0
            self._pos_index  = 0
            self._populate_table([])
            self.heatmap.update_heatmap([])
            self.lbl_db_count.setText("DB: 0 records")
            self._update_status("Database cleared.")

    # ------------------------------------------------------------------
    # Status bar helper
    # ------------------------------------------------------------------

    def _update_status(self, msg: str) -> None:
        self.lbl_status.setText(msg)

    # ------------------------------------------------------------------
    # Clean shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._stop_scan()
        event.accept()


# ============================================================================
# Entry point
# ============================================================================

def main() -> None:
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("WiFi3D Mapper")
    app.setOrganizationName("WiFi3DMapper")

    # Global dark palette so native controls also pick up the theme
    from PyQt6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(13, 17, 23))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(180, 200, 220))
    palette.setColor(QPalette.ColorRole.Base,            QColor(10, 14, 20))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(17, 22, 30))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(20, 28, 40))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(180, 200, 220))
    palette.setColor(QPalette.ColorRole.Text,            QColor(180, 200, 220))
    palette.setColor(QPalette.ColorRole.Button,          QColor(25, 35, 50))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(160, 190, 210))
    palette.setColor(QPalette.ColorRole.Link,            QColor(0, 160, 220))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(25, 55, 90))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(200, 225, 255))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()