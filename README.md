# 📡 WiFi Heatmap Analyzer
> **Visualize, Analyze, and Optimize your wireless network coverage with high-performance 2D/3D Heatmaps.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![UI](https://img.shields.io/badge/UI-PyQt6-brightgreen.svg)](https://www.qt.io/)
[![Hardware Acceleration](https://img.shields.io/badge/Graphics-OpenGL-red.svg)](https://www.opengl.org/)

## 🚀 Overview
**WiFi Heatmap Analyzer** is a powerful tool designed to map wireless signal strength throughout a physical space. By combining real-time WiFi scanning with advanced mathematical interpolation, it generates high-fidelity heatmaps that help identify "dead zones" and optimize router placement for maximum coverage.

This application leverages **PyQt6** for a modern GUI, **PyOpenGL** for hardware-accelerated 3D visualizations, and **SciPy** for spatial signal estimation.

---

## ✨ Key Features
- **Real-Time WiFi Scanning:** Interacts directly with system hardware to capture SSID, RSSI (signal strength), and frequency.
- **Advanced Interpolation:** Uses `scipy` algorithms to estimate signal strength in areas between data points, creating a smooth, accurate gradient.
- **2D & 3D Visualization:** Toggle between top-down 2D heatmaps and 3D terrain-style signal representations using `pyqtgraph` and `OpenGL`.
- **Persistent Database:** Saves signal surveys via a robust database layer (`database.py`) for comparison and long-term analysis.
- **Multi-Network Support:** Filter and analyze specific SSIDs or broadcast channels.

---

## 🛠️ Tech Stack
| Component | Technology |
| :--- | :--- |
| **Language** | Python 3.9+ |
| **GUI Framework** | PyQt6 |
| **Scanning** | `pywifi` |
| **Mathematics** | NumPy, SciPy (Interpolation) |
| **Visualization** | pyqtgraph, PyOpenGL, Matplotlib |
| **Data Handling** | Pandas, SQLite |

---

## 📂 Project Structure
- **`main.py`**: The entry point of the application. Orchestrates the UI and scanning logic.
- **`wifi_scanner.py`**: Handles low-level network interface communication to retrieve signal data.
- **`heatmap.py`**: The mathematical core; processes raw signal data into heatmap grids.
- **`visualization.py`**: Manages the rendering of 2D graphs and 3D signal surface plots.
- **`database.py`**: Manages storage and retrieval of historical scan data.

---

## ⚙️ Installation & Setup

### Prerequisites
*   **Administrative Privileges:** WiFi scanning requires permission to access the network card.
*   **Python:** 3.9 or higher.

### Steps
1. **Clone the repository:**
   ```bash
   git clone https://github.com/moazabeer/WiFi-Heatmap-Analyzer.git
   cd WiFi-Heatmap-Analyzer

2. **Install Dependencies:**
   ```bash
   pip install PyQt6 pyqtgraph PyOpenGL numpy scipy pandas pywifi matplotlib

3. **Run the Application: On Windows (Run as Admin):**
   ```python main.py
   On Linux/macOS:
   sudo python main.py
