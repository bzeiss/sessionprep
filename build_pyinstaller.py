"""
PyInstaller build script for SessionPrep.
Builds standalone executables using the shared build configuration.

Usage:
    uv run python build_pyinstaller.py [cli|gui|all] [--onefile] [--clean]
"""

import argparse
import os
import shutil
import subprocess
import sys
import platform

from build_conf import TARGETS, BASE_DIR, DIST_PYINSTALLER

DIST_DIR = os.path.join(BASE_DIR, DIST_PYINSTALLER)

def clean():
    """Remove previous build artifacts."""
    if os.path.isdir(DIST_DIR):
        print(f"Removing {DIST_DIR}")
        shutil.rmtree(DIST_DIR)



def _check_imports(target_key: str) -> list[str]:
    """Check that hidden-import packages are installed."""
    from importlib.util import find_spec
    seen = set()
    missing = []
    
    # Check hidden imports defined in build_conf
    imports = TARGETS[target_key].get("pyinstaller_hidden_imports", [])
    
    for mod_name in imports:
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
    # Name from build_conf includes .exe extension on Windows, strip it for PyInstaller --name
    app_name_ext = target["name"]
    app_name = os.path.splitext(app_name_ext)[0]
    
    entry_point = os.path.join(BASE_DIR, target["script"])

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

    # Define consistent paths matching Nuitka structure
    # Work path: dist_pyinstaller/sessionprep-linux-x64.build
    work_path = os.path.join(DIST_DIR, f"{app_name}.build")
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", app_name,
        "--noconfirm",
        "--distpath", DIST_DIR,
        "--workpath", work_path,
        "--specpath", DIST_DIR,
        "--collect-all", "sessionpreplib",
        "--collect-all", "sessionprepgui",
        "--collect-all", "soundfile",
        "--hidden-import", "numpy",
    ]

    for imp in target.get("pyinstaller_hidden_imports", []):
        cmd.extend(["--hidden-import", imp])

    icon_path = target.get("icon")
    if icon_path and os.path.isfile(icon_path):
        cmd.extend(["--icon", icon_path])

    is_macos = platform.system() == "Darwin"
    windowed = target.get("pyinstaller_windowed", False)

    if windowed:
        cmd.append("--windowed")
    else:
        cmd.append("--console")

    # macOS: --onefile + --windowed is deprecated.
    if onefile and not (is_macos and windowed):
        cmd.append("--onefile")
    else:
        if onefile and is_macos and windowed:
            print("Note: macOS GUI always builds as onedir (.app bundle will be zipped)")
        cmd.append("--onedir")

    cmd.append(entry_point)

    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        print(f"\nBuild failed for {app_name} with exit code {result.returncode}", file=sys.stderr)
        return False

    # Check for output
    if onefile:
        exe_path = os.path.join(DIST_DIR, app_name_ext)
    else:
        # Onedir puts it in dist/APP_NAME/APP_NAME_EXT
        exe_path = os.path.join(DIST_DIR, app_name, app_name_ext)

    if os.path.isfile(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\nBuild successful: {exe_path}")
        print(f"Size: {size_mb:.1f} MB")
    else:
        print(f"\nBuild completed but executable not found at expected path: {exe_path}")

    # On macOS, --windowed always produces a .app bundle — zip it
    if is_macos and windowed:
        app_bundle = os.path.join(DIST_DIR, f"{app_name}.app")
        if os.path.isdir(app_bundle):
            zip_base = os.path.join(DIST_DIR, f"{app_name}")
            shutil.make_archive(zip_base, "zip", DIST_DIR, f"{app_name}.app")
            zip_path = f"{zip_base}.zip"
            zip_mb = os.path.getsize(zip_path) / (1024 * 1024)
            print(f"Zipped .app bundle: {zip_path} ({zip_mb:.1f} MB)")

    return True


def main():
    parser = argparse.ArgumentParser(description="Build SessionPrep with PyInstaller")
    # Positional argument to match build_nuitka.py style (optional)
    parser.add_argument("target", choices=["cli", "gui", "all"], default="all", nargs="?",
                        help="Which target to build (default: all)")
    
    parser.add_argument("--onefile", action="store_true",
                        help="Build single executables")
    parser.add_argument("--clean", action="store_true",
                        help="Clean build artifacts before building")
    
    args = parser.parse_args()

    if args.clean:
        clean()

    targets_to_build = []
    if args.target == "all":
        targets_to_build = ["cli", "gui"]
    else:
        targets_to_build = [args.target]

    failed = []
    for t in targets_to_build:
        if not build(t, onefile=args.onefile):
            failed.append(t)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"Done. Failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"Done. Built: {', '.join(targets_to_build)}")
        print(f"Output: {DIST_DIR}")


if __name__ == "__main__":
    main()