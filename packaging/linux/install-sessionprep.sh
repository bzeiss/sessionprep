#!/usr/bin/env bash
# install-sessionprep.sh — Install SessionPrep from the tar.gz bundle
#
# Usage:
#   ./install-sessionprep.sh              # installs to ~/.local (no sudo needed)
#   sudo ./install-sessionprep.sh /usr/local  # system-wide install
#
# What this script does:
#   1. Copies the CLI and GUI binaries to <prefix>/bin/
#   2. Copies the icon to <prefix>/share/pixmaps/
#   3. Writes a .desktop launcher to <prefix>/share/applications/
#      (substitutes the actual binary path into the Exec= line)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${1:-$HOME/.local}"
BIN_DIR="$INSTALL_DIR/bin"
SHARE_DIR="$INSTALL_DIR/share"

echo "Installing SessionPrep to $INSTALL_DIR ..."

mkdir -p "$BIN_DIR" "$SHARE_DIR/applications" "$SHARE_DIR/pixmaps"

install -m 755 "$SCRIPT_DIR/sessionprep"     "$BIN_DIR/sessionprep"
install -m 755 "$SCRIPT_DIR/sessionprep-gui" "$BIN_DIR/sessionprep-gui"
install -m 644 "$SCRIPT_DIR/sessionprep.png" "$SHARE_DIR/pixmaps/sessionprep.png"

sed "s|Exec=/usr/local/bin/sessionprep-gui|Exec=$BIN_DIR/sessionprep-gui|g" \
    "$SCRIPT_DIR/sessionprep.desktop" \
    > "$SHARE_DIR/applications/sessionprep.desktop"
chmod 644 "$SHARE_DIR/applications/sessionprep.desktop"

echo ""
echo "Done."
echo "  CLI: $BIN_DIR/sessionprep"
echo "  GUI: $BIN_DIR/sessionprep-gui"
echo ""
if [ "$INSTALL_DIR" = "$HOME/.local" ]; then
    echo "Make sure $BIN_DIR is in your PATH."
    echo "Add the following to your ~/.bashrc or ~/.profile if needed:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
