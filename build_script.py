"""
Build script for creating standalone SessionPrep executables via PyInstaller.

Usage:
    python build_script.py                    # Build both CLI and GUI (onedir)
    python build_script.py --onefile          # Build both as single executables
    python build_script.py --target cli       # Build CLI only
    python build_script.py --target gui       # Build GUI only
    python build_script.py --clean --onefile  # Clean first, then build both

Requires: pyinstaller (install via `uv sync` or `pip install pyinstaller`)
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT_DIR, "dist")
BUILD_DIR = os.path.join(ROOT_DIR, "build")

_PLATFORM_SUFFIXES = {
    "Windows": "win",
    "Darwin": "macos",
    "Linux": "linux",
}


def _platform_suffix() -> str:
    """Return platform-arch suffix, e.g. 'win-x64' or 'macos-arm64'."""
    plat = _PLATFORM_SUFFIXES.get(platform.system(), platform.system().lower())
    arch = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return f"{plat}-{arch}"


def _resolve_icon() -> str | None:
    """Return the best icon path for the current platform.

    Preference order:
    - Windows: .ico
    - macOS:   .icns > .png (PyInstaller + Pillow can convert .png)
    - Linux:   .png
    """
    res_dir = os.path.join(ROOT_DIR, "sessionprepgui", "res")
    system = platform.system()
    if system == "Windows":
        candidates = ["icon.ico", "icon.png"]
    elif system == "Darwin":
        candidates = ["icon.icns", "icon.png"]
    else:
        candidates = ["icon.png"]
    for name in candidates:
        path = os.path.join(res_dir, name)
        if os.path.isfile(path):
            return path
    return None


# Build targets: (name, entry_point, console_mode, extra hidden imports)
TARGETS = {
    "cli": {
        "name": f"sessionprep-{_platform_suffix()}",
        "entry_point": os.path.join(ROOT_DIR, "sessionprep.py"),
        "windowed": False,
        "hidden_imports": [
            "rich", "rich.console", "rich.table", "rich.panel", "rich.progress",
        ],
    },
    "gui": {
        "name": f"sessionprep-gui-{_platform_suffix()}",
        "entry_point": os.path.join(ROOT_DIR, "sessionprep-gui.py"),
        "windowed": True,
        "icon": _resolve_icon(),
        "hidden_imports": [
            "PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
            "sounddevice",
        ],
    },
}


def clean():
    """Remove previous build artifacts."""
    for d in [DIST_DIR, BUILD_DIR]:
        if os.path.isdir(d):
            print(f"Removing {d}")
            shutil.rmtree(d)
    for target in TARGETS.values():
        spec_file = os.path.join(ROOT_DIR, f"{target['name']}.spec")
        if os.path.isfile(spec_file):
            os.remove(spec_file)
            print(f"Removed {spec_file}")


def _check_imports(target_key: str) -> list[str]:
    """Check that hidden-import packages are installed.

    Uses find_spec() instead of import_module() to avoid triggering
    native library loads (e.g. PortAudio for sounddevice).

    Returns a list of missing top-level package names (empty if all OK).
    """
    from importlib.util import find_spec
    seen = set()
    missing = []
    for mod_name in TARGETS[target_key]["hidden_imports"]:
        top = mod_name.split(".")[0]
        if top in seen:
            continue
        seen.add(top)
        if find_spec(top) is None:
            missing.append(top)
    return missing


def build(target_key: str, onefile: bool = False):
    """Run PyInstaller to create one executable."""
    target = TARGETS[target_key]
    app_name = target["name"]
    entry_point = target["entry_point"]

    print(f"\n{'=' * 60}")
    print(f"Building: {app_name}")
    print(f"{'=' * 60}")

    missing = _check_imports(target_key)
    if missing:
        print(f"\nERROR: Required packages not installed: {', '.join(missing)}")
        if target_key == "gui":
            print("Install GUI dependencies first:  uv sync --extra gui")
        print()
        return False

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", app_name,
        "--noconfirm",
        "--collect-all", "sessionpreplib",
        "--collect-all", "sessionprepgui",
        "--collect-all", "soundfile",
        "--hidden-import", "numpy",
    ]

    for imp in target["hidden_imports"]:
        cmd.extend(["--hidden-import", imp])

    icon_path = target.get("icon")
    if icon_path and os.path.isfile(icon_path):
        cmd.extend(["--icon", icon_path])

    is_macos = platform.system() == "Darwin"

    if target["windowed"]:
        cmd.append("--windowed")
    else:
        cmd.append("--console")

    # macOS: --onefile + --windowed is deprecated (PyInstaller v7 will error).
    # Always use --onedir for windowed targets on macOS; the .app gets zipped.
    if onefile and not (is_macos and target["windowed"]):
        cmd.append("--onefile")
    else:
        if onefile and is_macos and target["windowed"]:
            print("Note: macOS GUI always builds as onedir (.app bundle will be zipped)")
        cmd.append("--onedir")

    cmd.append(entry_point)

    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0:
        print(f"\nBuild failed for {app_name} with exit code {result.returncode}", file=sys.stderr)
        return False

    is_windows = platform.system() == "Windows"
    ext = ".exe" if is_windows else ""

    if onefile:
        exe_path = os.path.join(DIST_DIR, f"{app_name}{ext}")
    else:
        exe_path = os.path.join(DIST_DIR, app_name, f"{app_name}{ext}")

    if os.path.isfile(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\nBuild successful: {exe_path}")
        print(f"Size: {size_mb:.1f} MB")
    else:
        print(f"\nBuild completed but executable not found at expected path.")
        print(f"Check {DIST_DIR} for output.")

    # On macOS, --windowed always produces a .app bundle — zip it
    if is_macos and target["windowed"]:
        app_bundle = os.path.join(DIST_DIR, f"{app_name}.app")
        if os.path.isdir(app_bundle):
            zip_base = os.path.join(DIST_DIR, f"{app_name}")
            shutil.make_archive(zip_base, "zip", DIST_DIR, f"{app_name}.app")
            zip_path = f"{zip_base}.zip"
            zip_mb = os.path.getsize(zip_path) / (1024 * 1024)
            print(f"Zipped .app bundle: {zip_path} ({zip_mb:.1f} MB)")

    return True


def main():
    parser = argparse.ArgumentParser(description="Build SessionPrep executables")
    parser.add_argument(
        "--onefile", action="store_true",
        help="Build single executables (slower startup, simpler distribution)",
    )
    parser.add_argument(
        "--target", choices=["cli", "gui", "all"], default="all",
        help="Which target to build (default: all)",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Clean build artifacts before building",
    )
    parser.add_argument(
        "--clean-only", action="store_true",
        help="Only clean, don't build",
    )
    args = parser.parse_args()

    if args.clean or args.clean_only:
        clean()
        if args.clean_only:
            return

    targets = list(TARGETS.keys()) if args.target == "all" else [args.target]
    failed = []
    for t in targets:
        if not build(t, onefile=args.onefile):
            failed.append(t)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"Done. Failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"Done. Built: {', '.join(targets)}")
        print(f"Output: {DIST_DIR}")


if __name__ == "__main__":
    main()
