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
from build_conf import TARGETS, BASE_DIR, DIST_NUITKA

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
    # if sys.platform == "linux":
    #    # Let Nuitka decide for Python 3.13
    #    pass 
    
    if not target["console"]:
        if sys.platform == "win32":
            cmd.append("--windows-disable-console")
        elif sys.platform == "darwin":
            cmd.append("--macos-disable-console")
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
    
    print(f"[SUCCESS] Built {output_exe}")
    print(f"          Size: {os.path.getsize(output_exe) / (1024*1024):.2f} MB")

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