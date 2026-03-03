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

RPS_VERSION = "0.2.2"

def _check_dependencies(target_key):
    """Ensure required packages for the target are installed."""
    from importlib.util import find_spec

    # Check explicitly for PySide6 if it's the GUI target
    if target_key == "gui":
        if find_spec("PySide6") is None:
            print("\n[ERROR] PySide6 is missing. Run: uv sync --extra gui")
            sys.exit(1)

def fetch_and_bundle_rps(dist_dir, target):
    """Fetch RPS release binaries and bundle them with the executable."""
    import urllib.request
    import tarfile
    import zipfile
    from build_conf import get_platform_suffix
    
    suffix = get_platform_suffix()
    if sys.platform in ("win32", "darwin"):
        ext = "zip"
    else:
        ext = "tar.gz"
        
    url = f"https://github.com/bzeiss/rps/releases/download/{RPS_VERSION}/rps-{suffix}.{ext}"
    archive_path = os.path.join(dist_dir, f"rps-{suffix}.{ext}")
    
    print(f"\n[POST-PROCESS] Fetching RPS binaries for {suffix}...")
    try:
        if not os.path.exists(archive_path):
            print(f"               Downloading {url}")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(archive_path, 'wb') as out_file:
                shutil.copyfileobj(response, out_file)
    except Exception as e:
        print(f"               [WARNING] Could not download {url}: {e}")
        return
        
    # Determine the destination
    script_stem = os.path.splitext(os.path.basename(target["script"]))[0]
    if sys.platform == "darwin" and not target["console"]:
        dest_dir = os.path.join(dist_dir, f"{MACOS_APP_NAME}.app", "Contents", "MacOS")
    elif sys.platform in ("win32", "linux"):
        dest_dir = os.path.join(dist_dir, f"{script_stem}.dist")
    else:
        dest_dir = dist_dir
        
    print(f"               Extracting to {dest_dir}...")
    os.makedirs(dest_dir, exist_ok=True)
    
    binaries = ["rps-server", "rps-pluginscanner"]
    if sys.platform == "win32":
        binaries = [b + ".exe" for b in binaries]
        
    if ext == "zip":
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            for member in zip_ref.namelist():
                name = os.path.basename(member)
                if name in binaries:
                    source = zip_ref.open(member)
                    target_path = os.path.join(dest_dir, name)
                    with open(target_path, "wb") as target_file:
                        shutil.copyfileobj(source, target_file)
                    if sys.platform != "win32":
                        os.chmod(target_path, 0o755)
    else:
        with tarfile.open(archive_path, 'r:gz') as tar_ref:
            for member in tar_ref.getmembers():
                name = os.path.basename(member.name)
                if name in binaries:
                    source = tar_ref.extractfile(member)
                    target_path = os.path.join(dest_dir, name)
                    with open(target_path, "wb") as target_file:
                        shutil.copyfileobj(source, target_file)
                    os.chmod(target_path, 0o755)

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
        f"--output-filename={target['name']}",
        f"--output-dir={dist_dir}",
        "--assume-yes-for-downloads",
        # Optimizations
        "--lto=no",
    ]

    # Windows and Linux use directory mode (standalone) for faster startup.
    # macOS GUI uses .app bundle (configured below). macOS CLI is not built.

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
        print("[SUCCESS] Build completed")

    # Post processing step: Fetch and bundle RPS C++ plugins (GUI only)
    if not target["console"]:
        fetch_and_bundle_rps(dist_dir, target)

def main():
    parser = argparse.ArgumentParser(description="Build SessionPrep with Nuitka")
    parser.add_argument("target", choices=["cli", "gui", "all"], default="all", nargs="?")
    parser.add_argument("--clean", action="store_true", help="Clean cache before building")
    args = parser.parse_args()

    # Ensure dist folder exists
    os.makedirs(DIST_NUITKA, exist_ok=True)

    targets_to_build = []
    if args.target == "all":
        if sys.platform == "darwin":
            targets_to_build = ["gui"]  # macOS only ships the .app bundle
        else:
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
