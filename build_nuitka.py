"""
Nuitka build script for SessionPrep.
Builds standalone executables for CLI and GUI with optimized settings.

Usage:
    uv run python build_nuitka.py [cli|gui|all] [--clean]
"""
import sys
import os
import shutil
import subprocess
import argparse
from build_conf import TARGETS, BASE_DIR, DIST_NUITKA, MACOS_APP_NAME

def _check_dependencies(target_key):
    """Ensure required packages for the target are installed."""
    from importlib.util import find_spec
    
    # Check explicitly for PySide6 if it's the GUI target
    if target_key == "gui":
        if find_spec("PySide6") is None:
            print(f"\n[ERROR] PySide6 is missing. Run: uv sync --extra gui")
            sys.exit(1)

def run_nuitka(target_key, clean=False):
    _check_dependencies(target_key)
    target = TARGETS[target_key]
    script_path = os.path.join(BASE_DIR, target["script"])
    dist_dir = os.path.join(BASE_DIR, DIST_NUITKA)
    
    # Clean previous output or build artifacts if requested
    if clean:
        if os.path.exists(dist_dir):
            print(f"        Cleaning {dist_dir}...")
            shutil.rmtree(dist_dir)

    print(f"\n[BUILD] Building target: {target_key.upper()}")
    print(f"        Script: {script_path}")
    print(f"        Output: {target['name']}")
    
    # Base Nuitka command
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        f"--output-filename={target['name']}",
        f"--output-dir={dist_dir}",
        "--assume-yes-for-downloads",
        # Optimizations
        "--lto=no",
    ]
    
    # Platform specific flags
    if not target["console"]:
        if sys.platform == "win32":
            cmd.append("--windows-disable-console")
            icon_path = target.get("icon")
            if icon_path and os.path.isfile(icon_path):
                cmd.append(f"--windows-icon-from-ico={icon_path}")
        elif sys.platform == "darwin":
            # GUI on macOS: produce a proper .app bundle instead of a bare onefile binary
            cmd.remove("--onefile")
            cmd.append("--macos-create-app-bundle")
            cmd.append(f"--macos-app-name={MACOS_APP_NAME}")
            icon_path = target.get("icon")
            if icon_path and os.path.isfile(icon_path):
                cmd.append(f"--macos-app-icon={icon_path}")
        else:
            pass  # Linux GUI: keep --onefile
    # Plugins
    for plugin in target.get("nuitka_plugins", []):
        cmd.append(f"--enable-plugin={plugin}")

    # Exclusions (The "Clean Dependencies" Logic)
    for exclude in target.get("nuitka_exclude", []):
        cmd.append(f"--nofollow-import-to={exclude}")

    # Data inclusions
    for src, dest in target.get("include_data", []):
        cmd.append(f"--include-data-dir={src}={dest}")

    # Clean cache if requested (Nuitka's internal cache)
    if clean:
        cmd.append("--clean-cache=all")
        cmd.append("--remove-output")

    # Appending the script to compile
    cmd.append(script_path)

    # Clean previous output binary specifically
    output_exe = os.path.join(dist_dir, target['name'])
    if os.path.exists(output_exe):
        os.remove(output_exe)

    # Run
    print(f"        Command: {' '.join(cmd)}")
    subprocess.check_call(cmd)
    
    # Nuitka on Linux adds .bin suffix to avoid name collisions with source files.
    # We rename it back to the target name to match PyInstaller behavior.
    if sys.platform == "linux":
        bin_path = output_exe + ".bin"
        if os.path.exists(bin_path) and not os.path.exists(output_exe):
            print(f"        Renaming {bin_path} -> {output_exe}")
            os.rename(bin_path, output_exe)
    
    # On macOS GUI, output is a .app bundle (directory), not a single file.
    # Nuitka names the bundle from the script name, not --output-filename.
    # Rename it to MACOS_APP_NAME for a clean user-facing name.
    script_stem = os.path.splitext(os.path.basename(target["script"]))[0]
    nuitka_bundle = os.path.join(dist_dir, f"{script_stem}.app")
    app_bundle = os.path.join(dist_dir, f"{MACOS_APP_NAME}.app")
    if sys.platform == "darwin" and not target["console"] and os.path.isdir(nuitka_bundle):
        if os.path.exists(app_bundle):
            shutil.rmtree(app_bundle)
        os.rename(nuitka_bundle, app_bundle)
        print(f"[SUCCESS] Built {app_bundle}")
    elif os.path.isfile(output_exe):
        print(f"[SUCCESS] Built {output_exe}")
        print(f"          Size: {os.path.getsize(output_exe) / (1024*1024):.2f} MB")
    else:
        print(f"[SUCCESS] Build completed")

def main():
    parser = argparse.ArgumentParser(description="Build SessionPrep with Nuitka")
    parser.add_argument("target", choices=["cli", "gui", "all"], default="all", nargs="?")
    parser.add_argument("--clean", action="store_true", help="Clean cache before building")
    args = parser.parse_args()

    # Ensure dist folder exists
    os.makedirs(DIST_NUITKA, exist_ok=True)

    targets_to_build = []
    if args.target == "all":
        targets_to_build = ["cli", "gui"]
    else:
        targets_to_build = [args.target]

    for t in targets_to_build:
        try:
            run_nuitka(t, clean=args.clean)
        except subprocess.CalledProcessError as e:
            print(f"\n[ERROR] Build failed for {t}")
            sys.exit(1)

if __name__ == "__main__":
    main()