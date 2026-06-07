"""
visualization.py — WiFi3D Mapper
Contains two custom PyQtGraph widgets:
  • HeatmapWidget   — 3-D OpenGL surface plot with rotation / zoom
  • RSSIGraphWidget — scrolling 2-D line chart showing RSSI over time
"""

from matplotlib.pyplot import grid
import numpy as np
from typing import List, Dict, Optional, Deque
from collections import deque

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QSizePolicy
from PyQt6.QtCore    import Qt, QTimer
from PyQt6.QtGui     import QFont, QColor

import pyqtgraph as pg
import pyqtgraph.opengl as gl

from heatmap import HeatmapBuilder, rssi_to_color, RSSI_MIN, RSSI_MAX, build_colormap_array


# ---------------------------------------------------------------------------
# Shared palette  (mirrors main.py DARK_THEME)
# ---------------------------------------------------------------------------

BG_COLOR      = (13,  17,  23)     # near-black background
GRID_COLOR    = (40,  48,  58)
AXIS_COLOR    = (80, 100, 120)
TEXT_COLOR    = (180, 200, 220)
ACCENT_COLOR  = (0,  180, 255)


# ---------------------------------------------------------------------------
# 3-D Heatmap widget
# ---------------------------------------------------------------------------

class HeatmapWidget(QWidget):
    """
    Wraps a pyqtgraph.opengl.GLViewWidget and keeps a GLSurfacePlotItem
    up to date whenever new WiFi samples are provided.

    The user can:
      • Rotate  — left-click-drag
      • Pan     — middle-click-drag
      • Zoom    — scroll wheel
    (All built into GLViewWidget by default.)
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._builder = HeatmapBuilder()
        self._surface: Optional[gl.GLSurfacePlotItem] = None
        self._scatter: Optional[gl.GLScatterPlotItem] = None
        self._axes:    Optional[gl.GLAxisItem]        = None

        self._setup_ui()
        self._draw_empty_surface()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── OpenGL viewport ────────────────────────────────────────────
        self.view = gl.GLViewWidget()
        bg = QColor(*BG_COLOR)
        self.view.setBackgroundColor(bg)
        self.view.opts["center"] = pg.Vector(10.0, 10.0, 0)
        self.view.setCameraPosition(distance=50, elevation=35, azimuth=225)
        layout.addWidget(self.view, stretch=1)

        # ── Grid ───────────────────────────────────────────────────────
        grid = gl.GLGridItem()
        grid.setSize(x=20, y=20)
        grid.setSpacing(x=2, y=2, z=2)
        grid.translate(10.0, 10.0, 0)
        grid.setColor((*GRID_COLOR, 60))
        self.view.addItem(grid)
        # ── Axis lines ─────────────────────────────────────────────────
        

        # ── Colour legend (small 2D graph on top-right corner) ─────────
        self._legend = self._build_legend()
        legend_container = QWidget(self)
        legend_container.setFixedWidth(28)
        lv = QVBoxLayout(legend_container)
        lv.setContentsMargins(4, 8, 4, 8)
        lv.addWidget(self._legend)
        lv.addWidget(self._make_label("−30"), alignment=Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self._make_label("dBm"), alignment=Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self._make_label("−100"), alignment=Qt.AlignmentFlag.AlignCenter)

        # Overlay the legend on the GL viewport using an HBox trick
        legend_container.setParent(self.view)
        legend_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        legend_container.setFixedWidth(56)
        self._legend_container = legend_container
        legend_container.show()

    @staticmethod
    def _make_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: rgba({TEXT_COLOR[0]},{TEXT_COLOR[1]},{TEXT_COLOR[2]},180);")
        lbl.setFont(QFont("Consolas", 7))
        return lbl

    def _build_legend(self) -> pg.ImageItem:
        """Render a vertical colour ramp as a small PlotWidget."""
        pw = pg.PlotWidget()
        pw.setFixedWidth(20)
        pw.setMinimumHeight(120)
        pw.hideAxis("bottom")
        pw.hideAxis("left")
        pw.setBackground(tuple(BG_COLOR))

        cmap = build_colormap_array(128)        # (128, 4) float
        # Reshape to (1, 128, 4) image for ImageItem
        img_data = (cmap.reshape(1, 128, 4) * 255).astype(np.uint8)
        img_item = pg.ImageItem(img_data)
        pw.addItem(img_item)
        return pw

    # ------------------------------------------------------------------
    # Surface management
    # ------------------------------------------------------------------

    def _draw_empty_surface(self) -> None:
        """Show a flat dark-blue surface before any data arrives."""
        z, colors, gx, gy = self._builder.build([])
        self._redraw_surface(z, colors, gx, gy)

    def update_heatmap(self, samples: List[Dict]) -> None:
        """
        Recompute and redraw the 3-D surface from a fresh list of
        scan dictionaries.  Called from the main window on every scan tick.
        """
        z, colors, gx, gy = self._builder.build(samples)
        self._redraw_surface(z, colors, gx, gy)
        self._update_scatter(samples)

    def _redraw_surface(
        self,
        z:      np.ndarray,
        colors: np.ndarray,
        gx:     np.ndarray,
        gy:     np.ndarray,
    ) -> None:
        """Replace the GLSurfacePlotItem with freshly-computed data."""
        # Remove old surface
        if self._surface is not None:
            self.view.removeItem(self._surface)

        # GLSurfacePlotItem expects x/y as 1-D arrays when using setData
        xs = gx[0, :].astype(np.float32) - 10.0          # shape (grid_res,)
        ys = gy[:, 0].astype(np.float32) - 10.0          # shape (grid_res,)
        z32 = z.astype(np.float32)
        # colors must be (grid_res, grid_res, 4) uint8 or float
        colors32 = colors.astype(np.float32)

        self._surface = gl.GLSurfacePlotItem(
            x=xs, y=ys, z=z32, colors=colors32,
            shader="shaded",
            smooth=True,
            drawEdges=False,
        )
        self.view.addItem(self._surface)

    def _update_scatter(self, samples: List[Dict]) -> None:
        """Draw spheres at each measurement point."""
        if not samples:
            return

        xs = [s["x_pos"] for s in samples]
        ys = [s["y_pos"] for s in samples]

        print(
            f"x range: {min(xs):.2f} → {max(xs):.2f} | "
            f"y range: {min(ys):.2f} → {max(ys):.2f}"
        )

        pos = np.array(
            [[s["x_pos"] - 10.0,
              s["y_pos"] - 10.0,
              0.0] for s in samples],
            dtype=np.float32,
        )

        colors = np.array(
            [rssi_to_color(s["signal_strength"]) for s in samples],
            dtype=np.float32,
        )
        sizes = np.full(len(samples), 8.0, dtype=np.float32)

        if self._scatter is not None:
            self.view.removeItem(self._scatter)

        self._scatter = gl.GLScatterPlotItem(pos=pos, color=colors, size=sizes)
        self.view.addItem(self._scatter)

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def save_screenshot(self, filepath: str) -> None:
        """Capture the current OpenGL frame to *filepath*."""
        img = self.view.renderToArray((self.view.width(), self.view.height()))
        from PyQt6.QtGui import QImage
        h, w, _ = img.shape
        qimg = QImage(img.data, w, h, QImage.Format.Format_RGBA8888)
        qimg.save(filepath)


# ---------------------------------------------------------------------------
# Live RSSI graph widget
# ---------------------------------------------------------------------------

class RSSIGraphWidget(QWidget):
    """
    Scrolling line chart that shows RSSI (dBm) over time for the top-N
    strongest networks discovered during the current session.
    """

    MAX_NETWORKS  = 6     # max number of simultaneous lines
    HISTORY_LEN   = 120   # data-points retained per network

    # Colour palette for the lines (cycling)
    _LINE_COLORS = [
        (0,   200, 255),   # cyan
        (0,   255, 140),   # green
        (255, 160,   0),   # amber
        (255,  60,  80),   # red
        (200,  80, 255),   # violet
        (255, 230,  50),   # yellow
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._histories: Dict[str, Deque[float]] = {}   # bssid → deque of rssi
        self._labels:    Dict[str, str]           = {}   # bssid → SSID label
        self._curves:    Dict[str, pg.PlotDataItem] = {}
        self._color_map: Dict[str, tuple]         = {}
        self._color_idx = 0
        self._setup_ui()

    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(tuple(BG_COLOR))
        self.plot.setLabel("left",   "RSSI (dBm)", color=f"rgb{TEXT_COLOR}")
        self.plot.setLabel("bottom", "Samples",    color=f"rgb{TEXT_COLOR}")
        self.plot.setYRange(RSSI_MIN, RSSI_MAX)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.getAxis("left").setTextPen(pg.mkPen(color=TEXT_COLOR))
        self.plot.getAxis("bottom").setTextPen(pg.mkPen(color=TEXT_COLOR))
        self.plot.getAxis("left").setPen(pg.mkPen(color=GRID_COLOR))
        self.plot.getAxis("bottom").setPen(pg.mkPen(color=GRID_COLOR))

        # Reference lines at common RSSI thresholds
        for level, label in [(-50, "Excellent"), (-70, "Fair"), (-90, "Weak")]:
            line = pg.InfiniteLine(
                pos=level, angle=0,
                pen=pg.mkPen(color=(60, 80, 100), style=Qt.PenStyle.DashLine, width=1),
                label=label,
                labelOpts={"color": (80, 110, 140), "position": 0.02},
            )
            self.plot.addItem(line)

        self.legend = self.plot.addLegend(
            offset=(10, 10),
            brush=pg.mkBrush(color=(20, 28, 38, 200)),
            pen=pg.mkPen(color=GRID_COLOR),
            labelTextColor=TEXT_COLOR,
        )

        layout.addWidget(self.plot)

    # ------------------------------------------------------------------

    def update_networks(self, networks: List[Dict]) -> None:
        """
        Feed a fresh list of scanned networks into the chart.
        Only the top-N (by signal) are tracked to avoid clutter.
        """
        # Limit to strongest MAX_NETWORKS
        top = networks[:self.MAX_NETWORKS]

        for net in top:
            bssid = net["bssid"]
            rssi  = float(net["signal_strength"])
            ssid  = net.get("ssid", bssid)

            if bssid not in self._histories:
                if len(self._histories) >= self.MAX_NETWORKS:
                    continue  # already at cap
                self._histories[bssid] = deque(maxlen=self.HISTORY_LEN)
                self._labels[bssid]    = ssid

                # Assign next colour
                col = self._LINE_COLORS[self._color_idx % len(self._LINE_COLORS)]
                self._color_idx += 1
                self._color_map[bssid] = col

                curve = self.plot.plot(
                    pen=pg.mkPen(color=col, width=2),
                    name=f"{ssid[:18]}…" if len(ssid) > 18 else ssid,
                )
                self._curves[bssid] = curve

            self._histories[bssid].append(rssi)

        # Redraw all tracked curves
        for bssid, history in self._histories.items():
            if bssid in self._curves:
                y = np.array(history, dtype=np.float32)
                x = np.arange(len(y), dtype=np.float32)
                self._curves[bssid].setData(x=x, y=y)

    def clear(self) -> None:
        """Remove all curves and reset internal state."""
        for curve in self._curves.values():
            self.plot.removeItem(curve)
        self.legend.clear()
        self._histories.clear()
        self._labels.clear()
        self._curves.clear()
        self._color_map.clear()
        self._color_idx = 0