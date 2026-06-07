"""
wifi_scanner.py — WiFi3D Mapper
Cross-platform WiFi scanner that emits scan results via a PyQt6 signal.
Supports Windows (pywifi / netsh fallback) and Linux (pywifi / iwlist fallback).
"""

import sys
import re
import subprocess
import time
import platform
from typing import List, Dict

from PyQt6.QtCore import QThread, pyqtSignal

# ---------------------------------------------------------------------------
# Optional pywifi import — graceful degradation to OS-level fallbacks
# ---------------------------------------------------------------------------
try:
    import pywifi
    from pywifi import const as pywifi_const
    _PYWIFI_AVAILABLE = True
except Exception:
    _PYWIFI_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helper: parse security from pywifi AKM list
# ---------------------------------------------------------------------------

_AKM_NAMES = {
    0: "OPEN",
    1: "WPA",
    2: "WPA2-PSK",
    3: "WPA2-ENT",
    4: "WPA3",
}

def _akm_to_str(akm_list: list) -> str:
    """Convert a list of pywifi AKM constants to a human-readable string."""
    if not akm_list:
        return "OPEN"
    labels = [_AKM_NAMES.get(a, f"AKM-{a}") for a in akm_list]
    return "/".join(sorted(set(labels)))


# ---------------------------------------------------------------------------
# pywifi-based scanner
# ---------------------------------------------------------------------------

def _scan_pywifi() -> List[Dict]:
    """Use the pywifi library to scan for networks."""
    results: List[Dict] = []
    try:
        wifi = pywifi.PyWiFi()
        iface = wifi.interfaces()[0]
        iface.scan()
        time.sleep(0.5)          # give the driver time to populate results
        scan_results = iface.scan_results()

        for net in scan_results:
            ssid = net.ssid.strip() or "<hidden>"
            bssid = net.bssid.upper() if net.bssid else "00:00:00:00:00:00"
            # pywifi returns signal in dBm (negative integer) on most platforms
            rssi = int(net.signal) if net.signal else -100
            # Frequency → channel conversion
            freq = getattr(net, "freq", 0)
            channel = _freq_to_channel(freq)
            security = _akm_to_str(getattr(net, "akm", []))

            results.append({
                "ssid": ssid,
                "bssid": bssid,
                "signal_strength": rssi,
                "channel": channel,
                "security": security,
            })
    except Exception as exc:
        print(f"[pywifi] scan error: {exc}")

    return results


# ---------------------------------------------------------------------------
# Windows netsh fallback
# ---------------------------------------------------------------------------

def _scan_netsh() -> List[Dict]:
    """
    Parse `netsh wlan show networks mode=bssid` output.
    Returns list of network dicts.
    """
    results: List[Dict] = []
    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            stderr=subprocess.DEVNULL,
            timeout=8,
        ).decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"[netsh] error: {exc}")
        return results

    # Split on blank lines between network blocks
    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        ssid_m    = re.search(r"SSID\s+\d+\s*:\s*(.+)", block)
        bssid_m   = re.search(r"BSSID\s+\d+\s*:\s*([\dA-Fa-f:]+)", block)
        signal_m  = re.search(r"Signal\s*:\s*(\d+)%", block)
        channel_m = re.search(r"Channel\s*:\s*(\d+)", block)
        auth_m    = re.search(r"Authentication\s*:\s*(.+)", block)

        if not ssid_m:
            continue

        ssid    = ssid_m.group(1).strip() or "<hidden>"
        bssid   = bssid_m.group(1).upper() if bssid_m else "00:00:00:00:00:00"
        pct     = int(signal_m.group(1)) if signal_m else 0
        rssi    = _pct_to_dbm(pct)
        channel = int(channel_m.group(1)) if channel_m else 0
        security = auth_m.group(1).strip() if auth_m else "UNKNOWN"

        results.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal_strength": rssi,
            "channel": channel,
            "security": security,
        })
    return results


# ---------------------------------------------------------------------------
# Linux iwlist fallback
# ---------------------------------------------------------------------------

def _scan_iwlist() -> List[Dict]:
    """
    Parse `iwlist scan` output.
    Tries common interface names; falls back to the first available.
    """
    results: List[Dict] = []
    ifaces = ["wlan0", "wlp2s0", "wlp3s0", "wlo1"]
    raw = ""

    for iface in ifaces:
        try:
            raw = subprocess.check_output(
                ["iwlist", iface, "scan"],
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).decode("utf-8", errors="replace")
            if "Scan completed" in raw:
                break
        except Exception:
            continue

    if not raw:
        return results

    # Each cell is one AP
    cells = re.split(r"Cell \d+ - ", raw)[1:]
    for cell in cells:
        ssid_m    = re.search(r'ESSID:"([^"]*)"', cell)
        bssid_m   = re.search(r"Address:\s*([\dA-Fa-f:]+)", cell)
        signal_m  = re.search(r"Signal level=(-?\d+)\s*dBm", cell)
        signal_m2 = re.search(r"Signal level=(\d+)/100", cell)
        channel_m = re.search(r"Channel:(\d+)", cell)
        enc_m     = re.search(r"Encryption key:(on|off)", cell)
        ie_m      = re.search(r"IE: (?:IEEE 802\.11i/|WPA Version \d|WPA2)", cell)

        ssid    = ssid_m.group(1).strip() if ssid_m else "<hidden>"
        bssid   = bssid_m.group(1).upper() if bssid_m else "00:00:00:00:00:00"
        channel = int(channel_m.group(1)) if channel_m else 0

        if signal_m:
            rssi = int(signal_m.group(1))
        elif signal_m2:
            rssi = _pct_to_dbm(int(signal_m2.group(1)))
        else:
            rssi = -100

        if ie_m:
            security = "WPA2"
        elif enc_m and enc_m.group(1) == "on":
            security = "WEP/WPA"
        else:
            security = "OPEN"

        results.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal_strength": rssi,
            "channel": channel,
            "security": security,
        })
    return results


# ---------------------------------------------------------------------------
# Utility converters
# ---------------------------------------------------------------------------

def _pct_to_dbm(pct: int) -> int:
    """
    Convert Windows signal quality (0-100 %) to approximate dBm.
    Formula: dBm = (pct / 2) - 100   →   0% = -100 dBm, 100% = -50 dBm
    """
    return int((pct / 2) - 100)


def _freq_to_channel(freq_mhz: int) -> int:
    """Convert centre frequency (MHz) to 802.11 channel number."""
    if freq_mhz <= 0:
        return 0
    if 2412 <= freq_mhz <= 2484:
        if freq_mhz == 2484:
            return 14
        return (freq_mhz - 2412) // 5 + 1
    if 5170 <= freq_mhz <= 5825:
        return (freq_mhz - 5000) // 5
    return 0


# ---------------------------------------------------------------------------
# Platform dispatcher
# ---------------------------------------------------------------------------

def _scan_networks() -> List[Dict]:
    """
    Scan for nearby WiFi networks using the best available method.
    Priority:  pywifi  →  netsh (Windows)  →  iwlist (Linux)
    """
    if _PYWIFI_AVAILABLE:
        data = _scan_pywifi()
        if data:
            return data  # success — return immediately

    os_name = platform.system()
    if os_name == "Windows":
        return _scan_netsh()
    elif os_name == "Linux":
        return _scan_iwlist()
    return []


# ---------------------------------------------------------------------------
# QThread worker
# ---------------------------------------------------------------------------

class WiFiScanner(QThread):
    """
    Background thread that scans for WiFi networks at a fixed interval and
    emits results via Qt signals so the GUI can update without blocking.

    Signals
    -------
    networks_found : list[dict]
        Emitted each time a scan completes.  The list is sorted by
        signal_strength descending.
    scan_error : str
        Emitted if a scan attempt raises an unexpected exception.
    """

    networks_found: pyqtSignal = pyqtSignal(list)
    scan_error:     pyqtSignal = pyqtSignal(str)

    # Scan interval in seconds
    INTERVAL: float = 2.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = False

    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main loop — called automatically when QThread.start() is invoked."""
        self._running = True
        while self._running:
            try:
                networks = _scan_networks()
                # Sort strongest signal first
                networks.sort(key=lambda n: n["signal_strength"], reverse=True)
                self.networks_found.emit(networks)
            except Exception as exc:
                self.scan_error.emit(str(exc))

            # Sleep in small increments so stop() is responsive
            deadline = time.monotonic() + self.INTERVAL
            while self._running and time.monotonic() < deadline:
                time.sleep(0.1)

    def stop(self) -> None:
        """Request the scan loop to exit and wait for the thread to finish."""
        self._running = False
        self.wait(5000)  # up to 5 s