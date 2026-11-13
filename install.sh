#!/bin/sh
# Shokzer installer for your user account, on any Linux with Python 3.12+ and BlueZ.
#
#   curl -fsSL https://raw.githubusercontent.com/merkourio/shokzer/main/install.sh | sh
#   sh install.sh --uninstall
#
# Installs into ~/.local without sudo. If the GTK pieces the app needs are missing, it installs
# the command-line tool only and prints the command that adds them.
set -eu

VERSION="0.1.0"
REPO="https://github.com/merkourio/shokzer"
APP_ID="pm.markos.Shokzer"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA/shokzer"
BIN="$HOME/.local/bin"

say() { printf '%s\n' "$*"; }
fail() {
    printf 'shokzer install: %s\n' "$*" >&2
    exit 1
}

gui_packages() {
    # shellcheck disable=SC1091
    . /etc/os-release 2>/dev/null || true
    case " ${ID:-} ${ID_LIKE:-} " in
    *" fedora "* | *" rhel "*) say "  sudo dnf install python3-gobject gtk4 libadwaita" ;;
    *" debian "* | *" ubuntu "*) say "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" ;;
    *" arch "*) say "  sudo pacman -S python-gobject gtk4 libadwaita" ;;
    *" suse "* | *" opensuse "*) say "  sudo zypper install python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1" ;;
    *) say "  Install PyGObject 3.50+, GTK 4 and libadwaita 1.4+ with your package manager." ;;
    esac
}

have_gui_stack() {
    python3 - <<'PY' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import gi.events  # PyGObject 3.50+
from gi.repository import Adw
raise SystemExit((Adw.get_major_version(), Adw.get_minor_version()) < (1, 4))
PY
}

uninstall() {
    for link in "$BIN/shokzer" "$BIN/shokzer-gui"; do
        if [ -L "$link" ]; then
            case "$(readlink "$link")" in "$PREFIX"/*) rm -f "$link" ;; esac
        fi
    done
    rm -f "$DATA/applications/$APP_ID.desktop" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
    rm -rf "$PREFIX"
    say "Shokzer is removed. Your headset settings and Bluetooth pairings are unchanged."
}

source_tree() {
    # Run from a clone: install that. Otherwise download the release.
    here=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || here=""
    if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] &&
        grep -q '^name = "shokzer"' "$here/pyproject.toml"; then
        printf '%s\n' "$here"
        return
    fi
    url="$REPO/archive/refs/tags/v$VERSION.tar.gz"
    say "Downloading Shokzer $VERSION from $url" >&2
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" | tar -xz -C "$WORK"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$url" | tar -xz -C "$WORK"
    else
        fail "curl or wget is needed to download Shokzer."
    fi
    printf '%s\n' "$WORK/shokzer-$VERSION"
}

install() {
    command -v python3 >/dev/null 2>&1 || fail "python3 isn't installed. Install Python 3.12 or newer."
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
        fail "Python 3.12 or newer is needed; this system has $(python3 -V 2>&1)."
    python3 -c 'import ensurepip, venv' 2>/dev/null ||
        fail "Python's venv module is missing. On Debian or Ubuntu: sudo apt install python3-venv"

    if have_gui_stack; then gui=yes; else gui=no; fi

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    src=$(source_tree)
    [ -d "$src" ] || fail "couldn't get the Shokzer source."

    say "Installing into $PREFIX"
    rm -rf "$PREFIX/venv"
    mkdir -p "$PREFIX"
    # System site packages, so the app sees the system's PyGObject and GTK bindings.
    python3 -m venv --system-site-packages "$PREFIX/venv"
    "$PREFIX/venv/bin/python" -m pip install --quiet --disable-pip-version-check "$src"

    mkdir -p "$BIN"
    ln -sf "$PREFIX/venv/bin/shokzer" "$BIN/shokzer"
    if [ "$gui" = yes ]; then
        ln -sf "$PREFIX/venv/bin/shokzer-gui" "$BIN/shokzer-gui"
        mkdir -p "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
        sed "s|^Exec=.*|Exec=\"$PREFIX/venv/bin/shokzer-gui\"|" \
            "$src/data/$APP_ID.desktop" >"$DATA/applications/$APP_ID.desktop"
        cp "$src/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/"
        if command -v update-desktop-database >/dev/null 2>&1; then
            update-desktop-database -q "$DATA/applications" || true
        fi
    fi

    version=$("$PREFIX/venv/bin/shokzer" --version)
    say ""
    say "$version is installed."
    say "  Command line: shokzer status"
    if [ "$gui" = yes ]; then
        say "  App: open Shokzer from your applications, or run shokzer-gui"
    else
        say ""
        say "The app needs GTK pieces this system doesn't have yet. Install them with:"
        gui_packages
        say "then run this installer again. The command-line tool works already."
    fi
    case ":$PATH:" in
    *":$BIN:"*) ;;
    *) say "" && say "Add $BIN to your PATH to use the shokzer command." ;;
    esac
    say "Uninstall later with: sh install.sh --uninstall"
}

case "${1:-}" in
--uninstall) uninstall ;;
-h | --help) sed -n '2,9p' "$0" ;;
"") install ;;
*) fail "unknown option $1 (use --uninstall or --help)" ;;
esac
