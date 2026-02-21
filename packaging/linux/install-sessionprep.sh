#!/usr/bin/env bash
# install-sessionprep.sh — Install SessionPrep from the tar.gz bundle
#
# Usage:
#   ./install-sessionprep.sh [--help]
#   ./install-sessionprep.sh [PREFIX]              # default: ~/.local
#   sudo ./install-sessionprep.sh /usr/local       # system-wide
#   ./install-sessionprep.sh --uninstall [PREFIX]
#   sudo ./install-sessionprep.sh --uninstall /usr/local
#
# PREFIX and --uninstall may appear in any order.

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants — filenames and the placeholder in the bundled .desktop template
# ---------------------------------------------------------------------------

readonly CLI_BIN="sessionprep"
readonly GUI_BIN="sessionprep-gui"
readonly ICON_FILE="sessionprep.png"
readonly DESKTOP_FILE="sessionprep.desktop"
readonly DESKTOP_EXEC_PLACEHOLDER="Exec=/usr/local/bin/sessionprep-gui"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

die() {
    echo "Error: $*" >&2
    exit 1
}

# Check that all source files are present next to this script.
validate_sources() {
    local missing=0
    for f in "$CLI_BIN" "$GUI_BIN" "$ICON_FILE" "$DESKTOP_FILE"; do
        if [ ! -f "$SCRIPT_DIR/$f" ]; then
            echo "  Missing source file: $SCRIPT_DIR/$f" >&2
            missing=1
        fi
    done
    [ "$missing" -eq 0 ] || die "Source files missing. Run this script from inside the extracted archive."
}

# Verify we can write to the target prefix (fail before touching anything).
check_write_access() {
    if [ -d "$INSTALL_DIR" ] && [ ! -w "$INSTALL_DIR" ]; then
        die "No write permission for '$INSTALL_DIR'. Try: sudo $0 $INSTALL_DIR"
    fi
}

# Warn if BIN_DIR is not on PATH (applies to any prefix, not just ~/.local).
check_path() {
    case ":${PATH}:" in
        *":$BIN_DIR:"*) ;;
        *) echo "Note: $BIN_DIR is not in your PATH."
           echo "      Add to your shell profile:  export PATH=\"$BIN_DIR:\$PATH\""
           ;;
    esac
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

UNINSTALL=0
INSTALL_DIR=""

for arg in "$@"; do
    case "$arg" in
        --help|-h)    usage ;;
        --uninstall)  UNINSTALL=1 ;;
        -*)           die "Unknown option: $arg" ;;
        *)            [ -z "$INSTALL_DIR" ] || die "Unexpected argument: $arg"
                      INSTALL_DIR="$arg" ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.local}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="$INSTALL_DIR/bin"
PIXMAPS_DIR="$INSTALL_DIR/share/pixmaps"
APPS_DIR="$INSTALL_DIR/share/applications"

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

do_install() {
    validate_sources
    check_write_access

    echo "Installing SessionPrep to $INSTALL_DIR ..."

    mkdir -p "$BIN_DIR" "$APPS_DIR" "$PIXMAPS_DIR"

    install -m 755 "$SCRIPT_DIR/$CLI_BIN"   "$BIN_DIR/$CLI_BIN"
    install -m 755 "$SCRIPT_DIR/$GUI_BIN"   "$BIN_DIR/$GUI_BIN"
    install -m 644 "$SCRIPT_DIR/$ICON_FILE" "$PIXMAPS_DIR/$ICON_FILE"

    # Write .desktop atomically: generate into a temp file, then move into place.
    local tmp_desktop
    tmp_desktop="$(mktemp "$APPS_DIR/.sessionprep.desktop.XXXXXX")"
    sed "s|$DESKTOP_EXEC_PLACEHOLDER|Exec=$BIN_DIR/$GUI_BIN|g" \
        "$SCRIPT_DIR/$DESKTOP_FILE" > "$tmp_desktop"
    chmod 644 "$tmp_desktop"
    mv "$tmp_desktop" "$APPS_DIR/$DESKTOP_FILE"

    # Notify the desktop environment if the tool is available.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" 2>/dev/null || true
    fi

    echo ""
    echo "Done."
    echo "  CLI: $BIN_DIR/$CLI_BIN"
    echo "  GUI: $BIN_DIR/$GUI_BIN"
    echo ""
    check_path
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

do_uninstall() {
    check_write_access

    echo "Uninstalling SessionPrep from $INSTALL_DIR ..."

    local found
    found=0
    for f in \
        "$BIN_DIR/$CLI_BIN" \
        "$BIN_DIR/$GUI_BIN" \
        "$PIXMAPS_DIR/$ICON_FILE" \
        "$APPS_DIR/$DESKTOP_FILE"
    do
        if [ -f "$f" ]; then
            rm -f "$f"
            echo "  Removed: $f"
            found=1
        fi
    done

    if [ "$found" -eq 0 ]; then
        echo "  Nothing found to remove in $INSTALL_DIR."
    else
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database "$APPS_DIR" 2>/dev/null || true
        fi
        echo ""
        echo "Done."
    fi
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if [ "$UNINSTALL" -eq 1 ]; then
    do_uninstall
else
    do_install
fi
