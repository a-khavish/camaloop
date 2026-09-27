#!/usr/bin/env bash
#
# Camaloop installer.
#
#   ./install.sh                 install for everyone, into /usr/local
#   ./install.sh --user          install for you only, into ~/.local
#   ./install.sh --no-deps       skip the package manager step
#   ./install.sh --uninstall     remove it again
#
# Works on any Linux with apt, dnf, pacman or zypper. On anything else it
# says which libraries to install and carries on.

set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# setup.sh passes the interpreter that actually has the libraries; on its own
# this script looks for one, because the python3 first on PATH may be a conda
# or virtualenv one that cannot see the distribution's packages.
PYTHON_BIN="${CAMALOOP_PYTHON:-}"
MODE=install
SCOPE=system
WITH_DEPS=1

RED=$'\033[31m'; GREEN=$'\033[32m'; AMBER=$'\033[33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s\n' "$AMBER" "$OFF" "$*"; }
ok()   { printf '%s  ok%s  %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '%s  !%s   %s\n' "$AMBER" "$OFF" "$*"; }
die()  { printf '%s  x%s   %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --user)      SCOPE=user ;;
    --no-deps)   WITH_DEPS=0 ;;
    --uninstall) MODE=uninstall ;;
    --python)    PYTHON_BIN="$2"; shift ;;
    -h|--help)   sed -n '3,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           die "Unknown option: $1" ;;
  esac
  shift
done

[ "$(uname -s)" = "Linux" ] || die "Camaloop talks to Video4Linux, so it only runs on Linux."
[ "$(id -u)" -ne 0 ] || die "Run this as your normal user. It asks for sudo when it needs to."

if [ "$SCOPE" = "user" ]; then
  PREFIX="$HOME/.local"
  SUDO=""
else
  PREFIX="/usr/local"
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    die "sudo is not available. Use ./install.sh --user instead."
  fi
fi

LIB_DIR="$PREFIX/lib/camaloop"
BIN="$PREFIX/bin/camaloop"
DESKTOP="$PREFIX/share/applications/camaloop.desktop"
ICON="$PREFIX/share/icons/hicolor/scalable/apps/camaloop.svg"

# ---------------------------------------------------------------- uninstall

if [ "$MODE" = "uninstall" ]; then
  step "Removing Camaloop"
  $SUDO rm -rf "$LIB_DIR"
  $SUDO rm -f "$BIN" "$DESKTOP" "$ICON"
  command -v update-desktop-database >/dev/null 2>&1 &&
    $SUDO update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
  ok "Removed. Your saved looks are still in ~/.config/camaloop"
  say "${DIM}Virtual cameras and the v4l2loopback driver were left alone.${OFF}"
  exit 0
fi

# ------------------------------------------------------------- dependencies

detect_manager() {
  for candidate in apt-get dnf pacman zypper; do
    command -v "$candidate" >/dev/null 2>&1 && { echo "$candidate"; return; }
  done
  echo ""
}

# Package names differ between releases (Ubuntu 25.04 dropped policykit-1),
# so each is checked against the archive first and the essential libraries
# are installed separately from the optional extras. One missing package
# must not stop everything else from installing.

pkg_exists() {
  local _out
  case "$1" in
    apt-get) _out="$(apt-cache policy "$2" 2>/dev/null)"
             case "$_out" in
               *"Candidate: (none)"*) return 1 ;;
               *"Candidate: "*)       return 0 ;;
               *)                     return 1 ;;
             esac ;;
    dnf)     $SUDO dnf -q list --available "$2" >/dev/null 2>&1 ||
             $SUDO dnf -q list --installed "$2" >/dev/null 2>&1 ;;
    pacman)  pacman -Si "$2" >/dev/null 2>&1 || pacman -Qi "$2" >/dev/null 2>&1 ;;
    zypper)  _out="$(zypper --non-interactive info "$2" 2>/dev/null)"
             case "$_out" in *"Name"*) return 0 ;; *) return 1 ;; esac ;;
    *)       return 1 ;;
  esac
}

pkg_filter() {
  local manager="$1"; shift
  local found=""
  for candidate in "$@"; do
    pkg_exists "$manager" "$candidate" && found="$found $candidate"
  done
  printf '%s' "${found# }"
}

pkg_install() {
  local manager="$1"; shift
  [ "$#" -eq 0 ] && return 0
  case "$manager" in
    apt-get) $SUDO apt-get install -y "$@" ;;
    dnf)     $SUDO dnf install -y "$@" ;;
    pacman)  $SUDO pacman -S --needed --noconfirm "$@" ;;
    zypper)  $SUDO zypper install -y "$@" ;;
    *)       return 1 ;;
  esac
}

install_deps() {
  local manager core extra found
  manager="$(detect_manager)"
  case "$manager" in
    apt-get)
      core="python3 python3-pyqt5 python3-opencv python3-numpy python3-pil"
      extra="v4l2loopback-dkms v4l2loopback-utils policykit-1 polkitd pkexec"
      $SUDO apt-get update || warn "apt could not refresh its package list"
      ;;
    dnf)
      core="python3 python3-qt5 python3-opencv python3-numpy python3-pillow"
      extra="akmod-v4l2loopback v4l2loopback-utils polkit"
      ;;
    pacman)
      core="python python-pyqt5 python-opencv python-numpy python-pillow"
      extra="v4l2loopback-dkms v4l2loopback-utils polkit"
      ;;
    zypper)
      core="python3 python3-qt5 python3-opencv python3-numpy python3-Pillow"
      extra="v4l2loopback-kmp-default v4l2loopback-utils polkit"
      ;;
    *)
      warn "No apt, dnf, pacman or zypper here, so dependencies are up to you:"
      say "    python3, PyQt5, OpenCV (cv2), NumPy"
      say "    the v4l2loopback kernel module, for virtual cameras"
      return
      ;;
  esac

  found="$(pkg_filter "$manager" $core)"
  if [ -n "$found" ] && pkg_install "$manager" $found; then
    ok "Libraries installed with $manager"
  else
    warn "Some libraries would not install; pip can supply them instead"
  fi

  found="$(pkg_filter "$manager" $extra)"
  if [ -n "$found" ] && pkg_install "$manager" $found; then
    ok "Driver and permission helper installed"
  else
    warn "The v4l2loopback driver did not install - virtual cameras need it"
  fi
}

if [ "$WITH_DEPS" -eq 1 ]; then
  step "Installing dependencies"
  install_deps
else
  step "Skipping dependencies, as asked"
fi

step "Checking the Python libraries"
if [ -n "$PYTHON_BIN" ]; then
  if "$PYTHON_BIN" -c "import PyQt5, cv2, numpy" >/dev/null 2>&1; then
    ok "Using the interpreter chosen for this install: $PYTHON_BIN"
  else
    warn "$PYTHON_BIN cannot import the libraries; looking for another"
    PYTHON_BIN=""
  fi
fi
found_python=""
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 \
                   python3.14 python3.13 python3.12 python3.11; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue
    if "$resolved" -c "import PyQt5, cv2, numpy" >/dev/null 2>&1; then
      found_python="$resolved"; break
    fi
  done
fi
if [ -n "$found_python" ]; then
  PYTHON_BIN="$found_python"
  if [ "$found_python" = "$(command -v python3)" ]; then
    ok "PyQt5, OpenCV and NumPy are all importable"
  else
    ok "PyQt5, OpenCV and NumPy found under $found_python"
    say "    The python3 first on your PATH does not have them; this one does."
  fi
else
  missing=""
  for module in PyQt5 cv2 numpy; do
    python3 -c "import $module" >/dev/null 2>&1 || missing="$missing $module"
  done
  warn "No Python here can import:$missing"
  say "    Try:  pip install --user -r requirements.txt"
  say "    On Ubuntu 24.04 and newer, pip may ask for --break-system-packages."
fi

# ----------------------------------------------------------------- the app

if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 /usr/bin/python3 python3.14 python3.13 python3.12 python3.11; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue
    if "$resolved" -c "import PyQt5, cv2, numpy" >/dev/null 2>&1; then
      PYTHON_BIN="$resolved"; break
    fi
  done
fi
[ -n "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

step "Installing Camaloop into $PREFIX"
$SUDO mkdir -p "$LIB_DIR" "$(dirname "$BIN")" \
               "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
$SUDO rm -rf "$LIB_DIR/camaloop"
$SUDO cp -r "$SOURCE_DIR/camaloop" "$LIB_DIR/"
$SUDO cp "$SOURCE_DIR/README.md" "$LIB_DIR/" 2>/dev/null || true
$SUDO find "$LIB_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

$SUDO tee "$BIN" >/dev/null <<EOF
#!/usr/bin/env bash
# $PYTHON_BIN is the interpreter that had PyQt5, OpenCV and NumPy at install time.
exec "$PYTHON_BIN" -c 'import sys; sys.path.insert(0, "$LIB_DIR"); from camaloop.app import main; sys.exit(main())' "\$@"
EOF
$SUDO chmod 755 "$BIN"
ok "Command installed: $BIN"
[ "$PYTHON_BIN" = "$(command -v python3)" ] || say "    It will run with $PYTHON_BIN"

$SUDO install -m 644 "$SOURCE_DIR/packaging/camaloop.desktop" "$DESKTOP"
$SUDO install -m 644 "$SOURCE_DIR/packaging/camaloop.svg" "$ICON"
command -v update-desktop-database >/dev/null 2>&1 &&
  $SUDO update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 &&
  $SUDO gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true
ok "Added to your application menu"

# ------------------------------------------------------------ camera access

step "Checking camera access"
case " $(id -nG "$USER" 2>/dev/null) " in *" video "*) IN_VIDEO=1 ;; *) IN_VIDEO=0 ;; esac
if [ "$IN_VIDEO" -eq 1 ]; then
  ok "You are in the video group"
else
  warn "You are not in the video group, so opening a camera may be refused."
  if [ -n "$SUDO" ]; then
    read -r -p "  Add $USER to the video group now? [Y/n] " reply
    case "${reply:-Y}" in
      [Yy]*|"") $SUDO usermod -aG video "$USER"
                ok "Added. Log out and back in for it to take effect." ;;
      *)        say "    Later:  sudo usermod -aG video $USER" ;;
    esac
  fi
fi

step "Checking the virtual camera driver"
if [ -d /sys/module/v4l2loopback ]; then
  version="$(cat /sys/module/v4l2loopback/version 2>/dev/null || echo unknown)"
  ok "v4l2loopback $version is loaded"
  if [ ! -e /dev/v4l2loopback ]; then
    say "${DIM}    This version has no control device, so adding or removing a"
    say "    camera reloads the driver and briefly interrupts the others.${OFF}"
  fi
elif modprobe -n v4l2loopback >/dev/null 2>&1; then
  ok "v4l2loopback is installed and will load when you make your first camera"
else
  warn "v4l2loopback is not installed. Virtual cameras will not work until it is."
  say "${DIM}    On Secure Boot machines a DKMS module must be signed and enrolled"
  say "    before the kernel will load it; the installer usually walks you through it.${OFF}"
fi

printf '\n%sCamaloop is installed.%s Start it from your applications menu, or run:\n\n' "$GREEN" "$OFF"
printf '    camaloop\n\n'
printf '%sTo remove it again:  %s/install.sh --uninstall%s\n' "$DIM" "$SOURCE_DIR" "$OFF"
