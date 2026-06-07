"""
heatmap.py — WiFi3D Mapper
Converts sparse WiFi samples into a smooth, interpolated 3-D surface using
SciPy's radial-basis-function (RBF) interpolation.
"""

import numpy as np
from scipy.interpolate import RBFInterpolator
from typing import List, Dict, Tuple, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Grid resolution: number of points along each axis
GRID_RESOLUTION = 60

# Room size in arbitrary "virtual" units  (maps to OpenGL world units)
ROOM_WIDTH  = 20.0   # X axis
ROOM_DEPTH  = 20.0   # Y axis

# dBm range we clamp signal strength to
RSSI_MIN = -100
RSSI_MAX = -30

# Minimum number of distinct sample positions required before we attempt
# RBF interpolation.  Below this threshold we return a flat surface.
MIN_SAMPLES_FOR_INTERP = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_rssi(rssi: float) -> float:
    """
    Map dBm value to a 0–1 float.
    -100 dBm → 0.0  (no signal / floor)
    -30  dBm → 1.0  (excellent signal)
    """
    clamped = max(RSSI_MIN, min(RSSI_MAX, rssi))
    return (clamped - RSSI_MIN) / (RSSI_MAX - RSSI_MIN)


def rssi_to_z(rssi: float, z_scale: float = 10.0) -> float:
    """
    Convert dBm to a Z coordinate in world units.
    z_scale controls how tall the "peaks" appear.
    """
    return _normalise_rssi(rssi) * z_scale


def rssi_to_color(rssi: float) -> Tuple[float, float, float, float]:
    """
    Map dBm to an RGBA colour for PyQtGraph.
    Colour ramp: blue (weak) → cyan → green → yellow → red (strong).
    Returns normalised floats (0–1).
    """
    t = _normalise_rssi(rssi)   # 0 = weak, 1 = strong

    # 4-stop gradient
    stops = [
        (0.00, (0.05, 0.05, 0.55, 1.0)),   # deep blue
        (0.33, (0.00, 0.75, 0.90, 1.0)),   # cyan
        (0.66, (0.10, 0.85, 0.10, 1.0)),   # green
        (1.00, (1.00, 0.20, 0.05, 1.0)),   # red
    ]

    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            alpha = (t - t0) / (t1 - t0) if (t1 - t0) > 0 else 0
            r = c0[0] + alpha * (c1[0] - c0[0])
            g = c0[1] + alpha * (c1[1] - c0[1])
            b = c0[2] + alpha * (c1[2] - c0[2])
            a = c0[3] + alpha * (c1[3] - c0[3])
            return (r, g, b, a)

    return stops[-1][1]


# ---------------------------------------------------------------------------
# Core interpolation
# ---------------------------------------------------------------------------

class HeatmapBuilder:
    """
    Accumulates WiFi samples and produces a dense, interpolated grid
    suitable for rendering as a PyQtGraph GLSurfacePlotItem.
    """

    def __init__(
        self,
        grid_res:   int   = GRID_RESOLUTION,
        room_w:     float = ROOM_WIDTH,
        room_d:     float = ROOM_DEPTH,
        z_scale:    float = 10.0,
    ) -> None:
        self.grid_res = grid_res
        self.room_w   = room_w
        self.room_d   = room_d
        self.z_scale  = z_scale

        # Pre-compute the regular XY grid (reused every frame)
        xs = np.linspace(0, room_w, grid_res)
        ys = np.linspace(0, room_d, grid_res)
        self._gx, self._gy = np.meshgrid(xs, ys)   # (grid_res × grid_res)

        # Flat query array for RBF  shape (N, 2)
        self._query_pts = np.column_stack(
            [self._gx.ravel(), self._gy.ravel()]
        )

        # Floor surface (all weak signal) used as fallback
        self._floor_z = np.full((grid_res, grid_res), rssi_to_z(RSSI_MIN, z_scale))

    # ------------------------------------------------------------------

    def build(self, samples: List[Dict]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Given a list of scan dicts (each must have x_pos, y_pos, signal_strength),
        return (Z, colors_rgba, grid_x, grid_y) ready for GLSurfacePlotItem.

        Returns
        -------
        z_grid  : np.ndarray  shape (grid_res, grid_res)  Z values
        colors  : np.ndarray  shape (grid_res, grid_res, 4) RGBA 0–1
        gx      : np.ndarray  grid X coordinates
        gy      : np.ndarray  grid Y coordinates
        """
        if len(samples) < MIN_SAMPLES_FOR_INTERP:
            return self._fallback_surface()

        # Scatter points
        pts_xy  = np.array([[s["x_pos"], s["y_pos"]]       for s in samples], dtype=float)
        print(
             f"[heatmap] samples={len(samples)} "
             f"unique_positions={len(np.unique(pts_xy, axis=0))}"
            )
        pts_rssi = np.array([s["signal_strength"]          for s in samples], dtype=float)

        # Remove duplicates that would make RBF ill-conditioned
        pts_xy, unique_idx = np.unique(pts_xy, axis=0, return_index=True)
        pts_rssi = pts_rssi[unique_idx]

        if len(pts_xy) < MIN_SAMPLES_FOR_INTERP:
            return self._fallback_surface()

        try:
            rbf = RBFInterpolator(
                pts_xy,
                pts_rssi,
                kernel="thin_plate_spline",
                smoothing=2.0,
            )
            rssi_flat = rbf(self._query_pts)
            # Clamp to valid range
            rssi_grid = np.clip(
                rssi_flat.reshape(self.grid_res, self.grid_res),
                RSSI_MIN,
                RSSI_MAX,
            )
        except Exception as exc:
            print(f"[heatmap] RBF error: {exc}")
            return self._fallback_surface()

        z_grid = rssi_to_z(rssi_grid, self.z_scale)   # broadcast over array

        # Build RGBA colour array
        colors = np.zeros((self.grid_res, self.grid_res, 4), dtype=float)
        for i in range(self.grid_res):
            for j in range(self.grid_res):
                colors[i, j] = rssi_to_color(rssi_grid[i, j])

        return z_grid, colors, self._gx, self._gy

    # ------------------------------------------------------------------

    def _fallback_surface(
        self,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return a flat near-zero surface when there are too few samples."""
        floor = self._floor_z.copy()
        colors = np.zeros((self.grid_res, self.grid_res, 4), dtype=float)
        colors[:, :] = rssi_to_color(RSSI_MIN)   # uniform deep-blue
        return floor, colors, self._gx, self._gy


# ---------------------------------------------------------------------------
# Colour map array for the legend
# ---------------------------------------------------------------------------

def build_colormap_array(n: int = 256) -> np.ndarray:
    """
    Return a (n, 4) float32 RGBA array for the colour-ramp legend.
    Index 0 = weakest signal, index n-1 = strongest.
    """
    rssi_range = np.linspace(RSSI_MIN, RSSI_MAX, n)
    out = np.zeros((n, 4), dtype=np.float32)
    for i, r in enumerate(rssi_range):
        out[i] = rssi_to_color(r)
    return out