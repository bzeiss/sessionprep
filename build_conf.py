"""
Shared build configuration for SessionPrep.
Used by both Nuitka and PyInstaller build scripts to ensure consistency.
"""
import os
import sys
import platform

# Core application metadata
APP_NAME = "sessionprep"
VERSION_FILE = os.path.join("sessionpreplib", "_version.py")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "sessionprepgui", "res")

# Output directory names
DIST_PYINSTALLER = "dist_pyinstaller"
DIST_NUITKA = "dist_nuitka"

# Platform Logic
_PLATFORM_SUFFIXES = {
    "Windows": "win",
    "Darwin": "macos",
    "Linux": "linux",
}

def get_platform_suffix() -> str:
    """Return platform-arch suffix, e.g. 'win-x64' or 'macos-arm64'."""
    plat = _PLATFORM_SUFFIXES.get(platform.system(), platform.system().lower())
    arch = platform.machine().lower()
    # Normalize arch names
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return f"{plat}-{arch}"

def get_executable_name(base_name):
    """Returns the full executable name with platform suffix and extension."""
    suffix = get_platform_suffix()
    full_name = f"{base_name}-{suffix}"
    if sys.platform == "win32":
        return f"{full_name}.exe"
    return full_name

def _resolve_icon() -> str | None:
    """Return the best icon path for the current platform."""
    res_dir = os.path.join(BASE_DIR, "sessionprepgui", "res")
    system = platform.system()
    if system == "Windows":
        candidates = ["sessionprep.ico", "sessionprep.png"]
    elif system == "Darwin":
        candidates = ["sessionprep.icns", "sessionprep.png"]
    else:
        candidates = ["sessionprep.png"]
    for name in candidates:
        path = os.path.join(res_dir, name)
        if os.path.isfile(path):
            return path
    return None

# Target Definitions
TARGETS = {
    "cli": {
        "script": "sessionprep.py",
        "name": get_executable_name("sessionprep"),
        "console": True,
        # Nuitka specific
        "nuitka_exclude": [
            "PySide6", "shiboken6", "sessionprepgui", "tkinter", "unittest"
        ],
        "nuitka_plugins": [],
        "include_data": [],
        
        # PyInstaller specific
        "pyinstaller_hidden_imports": [
            "rich", "rich.console", "rich.table", "rich.panel", "rich.progress"
        ],
        "pyinstaller_windowed": False,
    },
    "gui": {
        "script": "sessionprep-gui.py",
        "name": get_executable_name("sessionprep-gui"),
        "console": False, 
        "icon": _resolve_icon(),
        
        # Nuitka specific
        "nuitka_exclude": [
            "rich", "curses", "tkinter", "unittest", "pdb"
        ],
        "nuitka_plugins": ["pyside6"],
        "include_data": [
            (ICON_DIR, os.path.join("sessionprepgui", "res"))
        ],
        
        # PyInstaller specific
        "pyinstaller_hidden_imports": [
            "PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
            "sounddevice",
        ],
        "pyinstaller_windowed": True,
    }
}