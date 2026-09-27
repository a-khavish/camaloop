#!/usr/bin/env bash
#
#   bash setup.sh
#
# Does everything: installs the dependencies and the virtual camera driver,
# installs the app, sorts out camera permissions, makes a first virtual
# camera, tests the whole thing end to end, and offers to start it.
#
# Options:
#   --yes         answer yes to everything, never ask
#   --user        install into ~/.local instead of /usr/local
#   --no-launch   do not offer to start the app at the end
#   --skip-tests  install only, run no tests
#   --venv        put the libraries in a private virtual environment of the
#                 app's own, instead of installing them system-wide
#   --venv=PATH   use that virtual environment instead of making one
#   --no-venv     never fall back to a virtual environment
#   --extras      also install mediapipe, for replacing the background
#                 without a green screen

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSUME_YES=0
SCOPE_ARG=""
LAUNCH=1
RUN_TESTS=1
VENV_MODE=auto      # auto | force | never
VENV_DIR=""
EXTRAS=0            # also install the optional background-segmentation package

BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'
RED=$'\033[31m'; GREEN=$'\033[32m'; AMBER=$'\033[33m'; BLUE=$'\033[36m'

STEP=0
TOTAL=8
declare -a RESULTS=()
NEEDS_RELOGIN=0
NEEDS_REBOOT=0
FAILED=0

step()  { STEP=$((STEP + 1)); printf '\n%s[%d/%d]%s %s%s%s\n' "$BLUE" "$STEP" "$TOTAL" "$OFF" "$BOLD" "$*" "$OFF"; }
ok()    { printf '%s  ok%s  %s\n' "$GREEN" "$OFF" "$*"; RESULTS+=("ok|$*"); }
warn()  { printf '%s  !%s   %s\n' "$AMBER" "$OFF" "$*"; RESULTS+=("warn|$*"); }
fail()  { printf '%s  x%s   %s\n' "$RED" "$OFF" "$*"; RESULTS+=("fail|$*"); FAILED=1; }
note()  { printf '%s      %s%s\n' "$DIM" "$*" "$OFF"; }
die()   { printf '\n%s  x%s   %s\n\n' "$RED" "$OFF" "$*" >&2; exit 1; }

ask() {  # ask "question" -> 0 for yes
  [ "$ASSUME_YES" -eq 1 ] && return 0
  [ -t 0 ] || return 0
  local reply
  read -r -p "      $1 [Y/n] " reply
  case "${reply:-Y}" in [Yy]*|"") return 0 ;; *) return 1 ;; esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)     ASSUME_YES=1 ;;
    --user)       SCOPE_ARG="--user" ;;
    --no-launch)  LAUNCH=0 ;;
    --skip-tests) RUN_TESTS=0; TOTAL=6 ;;
    --venv)       VENV_MODE=force ;;
    --venv=*)     VENV_MODE=force; VENV_DIR="${1#--venv=}" ;;
    --no-venv)    VENV_MODE=never ;;
    --extras)     EXTRAS=1 ;;
    -h|--help)    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *)            die "Unknown option: $1" ;;
  esac
  shift
done

printf '\n%s  Camaloop setup%s\n' "$BOLD" "$OFF"
printf '%s  Live camera effects and virtual cameras for Linux%s\n' "$DIM" "$OFF"

# ---------------------------------------------------------------- 1. system

step "Checking this machine"

[ "$(uname -s)" = "Linux" ] || die "Camaloop speaks Video4Linux, so it needs Linux."
[ "$(id -u)" -ne 0 ] || die "Run this as your normal user. It will ask for sudo when needed."
ok "Linux $(uname -r) on $(uname -m)"

command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)'; then
  ok "Python $PYVER"
else
  die "Python $PYVER is too old; 3.8 or newer is needed."
fi

DISTRO="$( . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}" )"
MANAGER=""
for candidate in apt-get dnf pacman zypper apk xbps-install eopkg; do
  command -v "$candidate" >/dev/null 2>&1 && { MANAGER="$candidate"; break; }
done
if [ -n "$MANAGER" ]; then
  ok "$DISTRO, using $MANAGER"
else
  warn "$DISTRO - no known package manager, so libraries are up to you"
fi

if [ -n "$SCOPE_ARG" ]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
  note "You will be asked for your password once or twice."
else
  warn "No sudo here, so installing just for you"
  SCOPE_ARG="--user"
  SUDO=""
fi

# ---------------------------------------------------------- 2. dependencies

step "Installing the libraries and the virtual camera driver"

if [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then
  # MANAGER="skipped" is what the rest of the script reads; there is no
  # second flag to keep in step with it.
  MANAGER="skipped"
  if [ -n "$SCOPE_ARG" ]; then
    note "Installing just for you, so no system packages are touched."
  else
    warn "No sudo, so system packages are skipped"
  fi
  note "The libraries will come from a private environment instead."
fi

# Package names drift between releases - Ubuntu 25.04 dropped policykit-1,
# for one - so every name is checked against the archive before it is used,
# and the essential libraries are installed apart from the optional extras.
# One unavailable package must never stop the rest from being installed.

pkg_exists() {
  local _out
  case "$MANAGER" in
    apt-get) _out="$(apt-cache policy "$1" 2>/dev/null)"
             case "$_out" in
               *"Candidate: (none)"*) return 1 ;;
               *"Candidate: "*)       return 0 ;;
               *)                     return 1 ;;
             esac ;;
    dnf)     $SUDO dnf -q list --available "$1" >/dev/null 2>&1 ||
             $SUDO dnf -q list --installed "$1" >/dev/null 2>&1 ;;
    pacman)  pacman -Si "$1" >/dev/null 2>&1 || pacman -Qi "$1" >/dev/null 2>&1 ;;
    zypper)  _out="$(zypper --non-interactive info "$1" 2>/dev/null)"
             case "$_out" in *"Name"*) return 0 ;; *) return 1 ;; esac ;;
    apk)     _out="$(apk search -x "$1" 2>/dev/null)"
             [ -n "$_out" ] ;;
    xbps-install)
             _out="$(xbps-query -R --property=pkgver "$1" 2>/dev/null)"
             [ -n "$_out" ] ;;
    eopkg)   _out="$(eopkg info "$1" 2>/dev/null)"
             case "$_out" in *"not found"*) return 1 ;; *"Name"*) return 0 ;;
                             *) return 1 ;; esac ;;
    *)       return 1 ;;
  esac
}

pkg_filter() {
  local found=""
  for candidate in "$@"; do
    pkg_exists "$candidate" && found="$found $candidate"
  done
  printf '%s' "${found# }"
}

pkg_install() {
  [ "$#" -eq 0 ] && return 0
  case "$MANAGER" in
    apt-get) $SUDO apt-get install -y "$@" ;;
    dnf)     $SUDO dnf install -y "$@" ;;
    pacman)  $SUDO pacman -S --needed --noconfirm "$@" ;;
    zypper)  $SUDO zypper --non-interactive install "$@" ;;
    apk)     $SUDO apk add "$@" ;;
    xbps-install) $SUDO xbps-install -y "$@" ;;
    eopkg)   $SUDO eopkg install -y "$@" ;;
    *)       return 1 ;;
  esac
}

pkg_install_each() {          # prints whatever would not install
  local failed=""
  for one in "$@"; do
    pkg_install "$one" >/dev/null 2>&1 || failed="$failed $one"
  done
  printf '%s' "${failed# }"
}

case "$MANAGER" in
  apt-get)
    CORE="python3 python3-venv python3-pip python3-pyqt5 python3-opencv opencv-data python3-numpy python3-pil"
    # policykit-1 on older releases, polkitd and pkexec on newer ones
    EXTRA="v4l2loopback-dkms v4l2loopback-utils policykit-1 polkitd pkexec"
    DESKTOP="qtwayland5 python3-gi gir1.2-glib-2.0 gstreamer1.0-pipewire xdg-desktop-portal ffmpeg grim"
    $SUDO apt-get update -qq || warn "apt could not refresh its package list"
    ;;
  dnf)
    CORE="python3 python3-pip python3-qt5 python3-opencv opencv-data python3-numpy python3-pillow"
    EXTRA="akmod-v4l2loopback v4l2loopback-utils polkit"
    DESKTOP="qt5-qtwayland python3-gobject pipewire-gstreamer xdg-desktop-portal ffmpeg-free ffmpeg grim"
    ;;
  pacman)
    CORE="python python-pip python-pyqt5 python-opencv opencv-samples python-numpy python-pillow"
    EXTRA="v4l2loopback-dkms v4l2loopback-utils polkit"
    DESKTOP="qt5-wayland python-gobject gst-plugin-pipewire xdg-desktop-portal ffmpeg grim"
    ;;
  zypper)
    CORE="python3 python3-pip python3-qt5 python3-opencv opencv-devel python3-numpy python3-Pillow"
    EXTRA="v4l2loopback-kmp-default v4l2loopback-utils polkit"
    DESKTOP="libqt5-qtwayland python3-gobject gstreamer-plugin-pipewire xdg-desktop-portal ffmpeg grim"
    ;;
  apk)
    CORE="python3 py3-pip py3-qt5 py3-opencv py3-numpy py3-pillow"
    EXTRA="v4l2loopback-src v4l2loopback-lts v4l-utils polkit"
    DESKTOP="qt5-qtwayland py3-gobject3 gst-plugins-good xdg-desktop-portal ffmpeg grim"
    $SUDO apk update >/dev/null 2>&1 || warn "apk could not refresh its package list"
    ;;
  xbps-install)
    CORE="python3 python3-pip python3-PyQt5 python3-opencv python3-numpy python3-Pillow"
    EXTRA="v4l2loopback v4l-utils polkit"
    DESKTOP="qt5-wayland python3-gobject gst-plugins-good1 xdg-desktop-portal ffmpeg grim"
    $SUDO xbps-install -S >/dev/null 2>&1 || warn "xbps could not refresh its package list"
    ;;
  eopkg)
    CORE="python3 python3-pip pyqt5 python3-opencv python3-numpy python3-pillow"
    EXTRA="v4l2loopback polkit"
    DESKTOP="qt5-wayland python3-gobject xdg-desktop-portal ffmpeg grim"
    $SUDO eopkg update-repo >/dev/null 2>&1 || warn "eopkg could not refresh its repository"
    ;;
  skipped) CORE=""; EXTRA=""; DESKTOP="" ;;
  *)
    CORE=""; EXTRA=""; DESKTOP=""
    warn "Install these yourself: python3, PyQt5, OpenCV, NumPy, v4l2loopback"
    ;;
esac

if [ -n "$CORE" ]; then
  CORE_FOUND="$(pkg_filter $CORE)"
  if [ -z "$CORE_FOUND" ]; then
    warn "None of the expected library packages exist here; pip will be tried"
  elif pkg_install $CORE_FOUND; then
    ok "Libraries installed with $MANAGER"
  else
    LEFT="$(pkg_install_each $CORE_FOUND)"
    if [ -z "$LEFT" ]; then
      ok "Libraries installed with $MANAGER, one at a time"
    else
      warn "These would not install:$LEFT"
      note "pip will be tried for anything Python still cannot import."
    fi
  fi

  EXTRA_FOUND="$(pkg_filter $EXTRA)"
  if [ -z "$EXTRA_FOUND" ]; then
    warn "The v4l2loopback driver is not in this system's package list"
  elif pkg_install $EXTRA_FOUND; then
    ok "Driver and permission helper installed"
  else
    LEFT="$(pkg_install_each $EXTRA_FOUND)"
    if [ -z "$LEFT" ]; then
      ok "Driver and permission helper installed, one at a time"
    else
      warn "Optional packages that would not install:$LEFT"
      note "The app still works; only the parts they provide are affected."
    fi
  fi

  # Wayland, screen capture and the desktop portal. None of these are
  # needed for a camera and effects; they are what makes the app feel
  # native on Wayland and lets it use the screen as a camera.
  if [ -n "$DESKTOP" ]; then
    DESKTOP_FOUND="$(pkg_filter $DESKTOP)"
    if [ -z "$DESKTOP_FOUND" ]; then
      note "No Wayland or screen capture packages found for this system."
    elif pkg_install $DESKTOP_FOUND >/dev/null 2>&1; then
      ok "Wayland support and screen capture installed"
    else
      LEFT="$(pkg_install_each $DESKTOP_FOUND)"
      if [ -z "$LEFT" ]; then
        ok "Wayland support and screen capture installed, one at a time"
      else
        note "Not installed:$LEFT  (only screen capture on Wayland is affected)"
      fi
    fi
  fi
fi

# A virtual environment belonging to the app, kept beside its code. This is
# not the one you may have active in your shell: a desktop launcher starts
# with no environment activated, so the app has to own its interpreter or it
# would break the moment you launched it from the menu.

if [ -z "$VENV_DIR" ]; then
  if [ -n "$SCOPE_ARG" ]; then
    VENV_DIR="$HOME/.local/lib/camaloop/venv"
  else
    VENV_DIR="/usr/local/lib/camaloop/venv"
  fi
fi

venv_python() { printf '%s/bin/python3' "$1"; }

make_venv() {
  local dir="$1" shared="$2" args=""
  [ "$shared" = "shared" ] && args="--system-site-packages"

  $SUDO mkdir -p "$(dirname "$dir")" 2>/dev/null || true
  if ! $SUDO python3 -m venv $args "$dir" >/tmp/camaloop-venv.log 2>&1; then
    warn "Could not create the environment at $dir"
    tail -3 /tmp/camaloop-venv.log 2>/dev/null | sed 's/^/        /'
    note "On Debian and Ubuntu this usually means: sudo apt install python3-venv"
    return 1
  fi

  local py; py="$(venv_python "$dir")"
  # Anything already provided by the system is reused when shared, so only
  # what is genuinely missing gets downloaded.
  local wanted=""
  # "~=4.5" is the same requirement as ">=4.5,<5" without a "<" in it: the
  # list is expanded unquoted into the pip command below, and a reader
  # should not have to work out whether that would redirect.
  for pair in "PyQt5:PyQt5" "cv2:opencv-python~=4.5" "numpy:numpy" "PIL:Pillow"; do
    local module="${pair%%:*}" package="${pair#*:}"
    "$py" -c "import $module" >/dev/null 2>&1 || wanted="$wanted $package"
  done

  if [ -n "$wanted" ]; then
    note "Fetching into the environment:$wanted"
    if ! $SUDO "$py" -m pip install --upgrade $wanted >>/tmp/camaloop-venv.log 2>&1; then
      warn "pip could not install:$wanted"
      tail -4 /tmp/camaloop-venv.log 2>/dev/null | sed 's/^/        /'
      return 1
    fi
  fi
  [ -z "$(missing_for "$py")" ]
}

is_managed() {
  # PEP 668: the distribution owns this interpreter and pip will refuse it.
  "$1" -c 'import os, sys, sysconfig; sys.exit(0 if os.path.exists(
      os.path.join(sysconfig.get_paths()["stdlib"], "EXTERNALLY-MANAGED")) else 1)' \
      >/dev/null 2>&1
}

# The distribution installs its packages into /usr/bin/python3. If the
# python3 first on PATH is a conda, pyenv, Homebrew or virtualenv one, those
# packages are invisible to it - so every candidate interpreter is tried and
# the app is pointed at whichever actually has the libraries.

missing_for() {
  local interpreter="$1" missing=""
  for module in PyQt5 cv2 numpy; do
    "$interpreter" -c "import $module" >/dev/null 2>&1 || missing="$missing $module"
  done
  printf '%s' "${missing# }"
}

python_candidates() {
  local seen="" resolved
  for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 \
                   python3.14 python3.13 python3.12 python3.11 python3.10; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [ -n "$resolved" ] || continue
    case " $seen " in *" $resolved "*) continue ;; esac
    seen="$seen $resolved"
    printf '%s\n' "$resolved"
  done
}

PYTHON=""

# Extras go through pip, so an interpreter pip refuses to touch is no good.
if [ "$EXTRAS" -eq 1 ] && [ "$VENV_MODE" = "auto" ]; then
  FIRST_OK=""
  for interpreter in $(python_candidates); do
    if [ -z "$(missing_for "$interpreter")" ]; then FIRST_OK="$interpreter"; break; fi
  done
  if [ -n "$FIRST_OK" ] && is_managed "$FIRST_OK"; then
    VENV_MODE=force
    note "$FIRST_OK is managed by your distribution, so pip cannot add to it."
    note "The extras will go in an environment belonging to the app instead."
  fi
fi

if [ "$VENV_MODE" != "force" ]; then
  for interpreter in $(python_candidates); do
    if [ -z "$(missing_for "$interpreter")" ]; then
      PYTHON="$interpreter"
      break
    fi
  done
fi

if [ -n "$PYTHON" ]; then
  if [ "$PYTHON" != "$(command -v python3)" ]; then
    ok "Libraries found under $PYTHON"
    note "The python3 first on your PATH does not have them; this one does,"
    note "so Camaloop will be set up to use it."
  else
    ok "PyQt5, OpenCV and NumPy are ready"
  fi
elif [ "$VENV_MODE" = "never" ]; then
  DEFAULT_PY="$(command -v python3)"
  PYTHON="$DEFAULT_PY"
  fail "No Python here can import:$(missing_for "$DEFAULT_PY")"
  note "Re-run without --no-venv to let the app keep its own copy instead."
else
  if [ "$VENV_MODE" = "force" ]; then
    note "Setting up a private environment for the app, as asked."
  else
    warn "The system packages did not give a usable Python"
    note "Falling back to a private environment belonging to the app."
  fi

  # Sharing the system's packages first: it reuses the distribution's Qt,
  # which is both far smaller and better behaved on the desktop than the
  # copy of Qt that pip's PyQt5 brings with it.
  if make_venv "$VENV_DIR" shared; then
    PYTHON="$(venv_python "$VENV_DIR")"
    ok "Environment ready at $VENV_DIR (sharing the system's packages)"
  elif make_venv "${VENV_DIR}-isolated" isolated; then
    VENV_DIR="${VENV_DIR}-isolated"
    PYTHON="$(venv_python "$VENV_DIR")"
    ok "Environment ready at $VENV_DIR (self-contained)"
  else
    PYTHON="$(command -v python3)"
    fail "Could not build a working environment"
    note "The log is at /tmp/camaloop-venv.log"
  fi
fi

if [ -n "${VIRTUAL_ENV:-}" ]; then
  note "You have $VIRTUAL_ENV active. Camaloop deliberately does not"
  note "install into it: your menu entry starts with nothing activated,"
  note "so the app keeps an interpreter of its own and stays working."
fi
if [ "$EXTRAS" -eq 1 ] && [ -n "$PYTHON" ]; then
  EXTRA_PKGS=""
  "$PYTHON" -c "import mediapipe" >/dev/null 2>&1 || EXTRA_PKGS="$EXTRA_PKGS mediapipe"
  "$PYTHON" -c "import PIL" >/dev/null 2>&1 || EXTRA_PKGS="$EXTRA_PKGS Pillow"

  if [ -z "$EXTRA_PKGS" ]; then
    ok "The optional extras are already there"
  else
    note "Installing:$EXTRA_PKGS  (mediapipe is around 40 MB)"
    EXTRA_SUDO=""
    [ -w "$PYTHON" ] || [ -w "$(dirname "$PYTHON")" ] || EXTRA_SUDO="$SUDO"
    EXTRA_LOG=/tmp/camaloop-extras.log
    if $EXTRA_SUDO "$PYTHON" -m pip install $EXTRA_PKGS >"$EXTRA_LOG" 2>&1; then
      ok "Background segmentation is ready"
    elif $EXTRA_SUDO "$PYTHON" -m pip install --break-system-packages $EXTRA_PKGS \
           >>"$EXTRA_LOG" 2>&1; then
      ok "Background segmentation is ready"
    else
      warn "Could not install:$EXTRA_PKGS"
      tail -4 "$EXTRA_LOG" 2>/dev/null | sed "s/^/        /"
      note "The app works without them; only background replacement is affected."
    fi
  fi
fi
export CAMALOOP_PYTHON="$PYTHON"

# -------------------------------------------------------------- 3. the app

step "Installing Camaloop"

if bash "$HERE/install.sh" --no-deps $SCOPE_ARG >/tmp/camaloop-install.log 2>&1; then
  if [ -n "$SCOPE_ARG" ]; then
    BIN_PATH="$HOME/.local/bin/camaloop"
  else
    BIN_PATH="/usr/local/bin/camaloop"
  fi
  ok "Installed, command is $BIN_PATH"
else
  fail "Installation failed - see /tmp/camaloop-install.log"
  tail -5 /tmp/camaloop-install.log | sed 's/^/      /'
fi

case ":$PATH:" in *":$HOME/.local/bin:"*) ON_PATH=1 ;; *) ON_PATH=0 ;; esac
if [ -n "$SCOPE_ARG" ] && [ "$ON_PATH" -eq 0 ]; then
  warn "$HOME/.local/bin is not on your PATH"
  note "Add it with: echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.profile"
fi

# ------------------------------------------------------- 4. camera access

step "Setting up camera access"

case " $(id -nG "$USER" 2>/dev/null) " in *" video "*) IN_VIDEO=1 ;; *) IN_VIDEO=0 ;; esac
if [ "$IN_VIDEO" -eq 1 ]; then
  ok "You are already in the video group"
else
  if [ -n "$SUDO" ] && ask "Add $USER to the video group so cameras can be opened?"; then
    if $SUDO usermod -aG video "$USER"; then
      ok "Added to the video group"
      NEEDS_RELOGIN=1
      note "This only takes effect in a new login session."
    else
      fail "Could not add you to the video group"
    fi
  else
    warn "Not in the video group - opening a camera may be refused"
  fi
fi

# ------------------------------------------------------------- 5. driver

step "Loading the virtual camera driver"

SECURE_BOOT=0
if command -v mokutil >/dev/null 2>&1; then
  SB_STATE="$(mokutil --sb-state 2>/dev/null || true)"
  case "$SB_STATE" in *[Ee]nabled*) SECURE_BOOT=1 ;; esac
fi

if [ -d /sys/module/v4l2loopback ]; then
  ok "v4l2loopback $(cat /sys/module/v4l2loopback/version 2>/dev/null || echo '') is loaded"
elif [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
  MODPROBE_OUT="$($SUDO modprobe v4l2loopback 2>&1)"
  if [ -d /sys/module/v4l2loopback ]; then
    ok "Loaded v4l2loopback $(cat /sys/module/v4l2loopback/version 2>/dev/null || echo '')"
  elif case "$MODPROBE_OUT" in *"ey was rejected"*) true ;; *) false ;; esac; then
    fail "Secure Boot refused the driver because its key is not enrolled yet"
    note "Run: sudo dpkg-reconfigure v4l2loopback-dkms   (or reinstall the package)"
    note "then reboot and pick 'Enrol MOK' on the blue screen, and run this again."
    NEEDS_REBOOT=1
  else
    fail "Could not load v4l2loopback"
    # No pipe here on purpose: with pipefail set, a reader that stops early
    # sends SIGPIPE back to the writer and the script dies at exit 141 -
    # which would take it out at the exact moment it is reporting a failure.
    _shown=0
    while IFS= read -r _line; do
      [ "$_shown" -ge 3 ] && break
      printf '      %s\n' "$_line"
      _shown=$((_shown + 1))
    done <<< "$MODPROBE_OUT"
    [ "$SECURE_BOOT" -eq 1 ] && note "Secure Boot is on, which is the usual cause."
  fi
else
  warn "Cannot load the driver without sudo"
fi

DRIVER_READY=0
[ -d /sys/module/v4l2loopback ] && DRIVER_READY=1
if [ "$DRIVER_READY" -eq 1 ]; then
  if [ -e /dev/v4l2loopback ]; then
    note "This version adds and removes cameras one at a time."
  else
    note "This version reloads the driver on every change, so cameras must be idle."
  fi
fi

# ----------------------------------------------------- 6. a first camera

step "Making your first virtual camera"

if [ ! -d /sys/module/v4l2loopback ]; then
  warn "Skipped - the driver is not loaded"
elif python3 -c "
import sys; sys.path.insert(0, '$HERE')
from camaloop.core import loopback
sys.exit(0 if loopback.list_cameras() else 1)
" 2>/dev/null; then
  EXISTING="$(python3 -c "
import sys; sys.path.insert(0, '$HERE')
from camaloop.core import loopback
print(', '.join(f'{c.label} ({c.path})' for c in loopback.list_cameras()))
" 2>/dev/null)"
  ok "You already have: $EXISTING"
else
  NEW_INDEX="$(python3 -c "
import sys; sys.path.insert(0, '$HERE')
from camaloop.core import loopback
print(loopback.next_free_index())
" 2>/dev/null || echo 10)"
  if $SUDO modprobe -r v4l2loopback 2>/dev/null && \
     $SUDO modprobe v4l2loopback devices=1 "video_nr=$NEW_INDEX" \
       "card_label=Camaloop" exclusive_caps=1 2>/dev/null; then
    ok "Created \"Camaloop\" at /dev/video$NEW_INDEX"
  else
    warn "Could not create one automatically - make it in the app instead"
  fi
fi

# ----------------------------------------------------------- 7. the tests

if [ "$RUN_TESTS" -eq 1 ]; then
  step "Testing the effects"
  if python3 "$HERE/tools/selftest.py" >/tmp/camaloop-selftest.log 2>&1; then
    EFFECT_COUNT="$(grep -c '  ok    ' /tmp/camaloop-selftest.log || echo '?')"
    ok "All $EFFECT_COUNT effects ran cleanly"
    grep -E 'everything at once' /tmp/camaloop-selftest.log | sed 's/^/      /'
  else
    fail "Some effects failed - see /tmp/camaloop-selftest.log"
    tail -6 /tmp/camaloop-selftest.log | sed 's/^/      /'
  fi

  step "Testing the virtual camera end to end"
  python3 "$HERE/tools/verify_pipeline.py" 2>&1 | sed 's/^/  /'
  case "${PIPESTATUS[0]}" in
    0) ok "Frames went out to the virtual camera and came back intact" ;;
    2) warn "Skipped - no idle virtual camera to test with" ;;
    *) if [ "$NEEDS_RELOGIN" -eq 1 ]; then
         warn "Test failed, most likely because the video group needs a new login"
       else
         fail "The virtual camera did not pass the round-trip test"
       fi ;;
  esac
fi

# --------------------------------------------------------------- summary

printf '\n%s  Summary%s\n' "$BOLD" "$OFF"
printf '%s  ---------------------------------------------%s\n' "$DIM" "$OFF"
for entry in "${RESULTS[@]}"; do
  status="${entry%%|*}"; text="${entry#*|}"
  case "$status" in
    ok)   printf '  %s+%s %s\n' "$GREEN" "$OFF" "$text" ;;
    warn) printf '  %s!%s %s\n' "$AMBER" "$OFF" "$text" ;;
    fail) printf '  %sx%s %s\n' "$RED" "$OFF" "$text" ;;
  esac
done
printf '%s  ---------------------------------------------%s\n' "$DIM" "$OFF"

if [ "$NEEDS_REBOOT" -eq 1 ]; then
  printf '\n%sOne thing left:%s reboot and enrol the Secure Boot key, then run this again.\n' "$AMBER" "$OFF"
  exit 1
fi

if [ "$FAILED" -eq 1 ]; then
  printf '\n%sSome steps did not work.%s The app is installed and the lines marked x\n' "$AMBER" "$OFF"
  printf 'above say what to fix. Start it anyway with:  camaloop\n\n'
  exit 1
fi

if [ "$DRIVER_READY" -eq 1 ]; then
  printf '\n%sCamaloop is ready.%s\n\n' "$GREEN" "$OFF"
else
  printf '\n%sCamaloop is installed.%s Effects and preview work now. Sharing with\n' "$GREEN" "$OFF"
  printf 'other apps needs the v4l2loopback driver, which is not loaded yet.\n\n'
fi
if [ "$NEEDS_RELOGIN" -eq 1 ]; then
  printf '  %sLog out and back in first%s - the video group only applies to new sessions.\n\n' "$AMBER" "$OFF"
fi
printf '  Start it from your applications menu, or run:  %scamaloop%s\n' "$BOLD" "$OFF"
printf '  %sIn the app: pick your camera, press Start camera, switch on some\n' "$DIM"
printf '  effects, then press Share with other apps.%s\n\n' "$OFF"

if [ "$LAUNCH" -eq 1 ] && [ "$NEEDS_RELOGIN" -eq 0 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  if ask "Start Camaloop now?"; then
    setsid camaloop >/dev/null 2>&1 &
    printf '\n  Started.\n\n'
  fi
fi
exit 0
