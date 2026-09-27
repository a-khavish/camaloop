#!/usr/bin/env bash
#
# Build a .deb for Debian and Ubuntu:
#
#     ./packaging/build-deb.sh
#     sudo apt install ./camaloop_1.0.0_all.deb
#
# Only needs dpkg-deb, which is already on every Debian-based system.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/camaloop/__init__.py")"
[ -n "$VERSION" ] || { echo "Could not read the version number." >&2; exit 1; }

PKG="camaloop"
# Whoever builds the package owns it. Falls back to your git identity.
MAINTAINER="${MAINTAINER:-$(git config user.name 2>/dev/null || echo "Khavish Auckaloo") <$(git config user.email 2>/dev/null || echo 301292531+a-khavish@users.noreply.github.com)>}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "Building $PKG $VERSION"

install -d "$STAGE/DEBIAN" \
           "$STAGE/usr/share/$PKG" \
           "$STAGE/usr/bin" \
           "$STAGE/usr/share/applications" \
           "$STAGE/usr/share/icons/hicolor/scalable/apps" \
           "$STAGE/usr/share/doc/$PKG"

cp -r "$ROOT/camaloop" "$STAGE/usr/share/$PKG/"
# The diagnostics travel with the app, so --check works after install.
install -d "$STAGE/usr/share/$PKG/tools"
install -m 644 "$ROOT/tools/verify_pipeline.py" "$STAGE/usr/share/$PKG/tools/"
install -m 644 "$ROOT/tools/selftest.py" "$STAGE/usr/share/$PKG/tools/"
find "$STAGE/usr/share/$PKG" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$STAGE/usr/bin/$PKG" <<'EOF'
#!/usr/bin/env bash
# The distribution's packages live under /usr/bin/python3. If the python3
# first on PATH is a conda, pyenv or virtualenv one it will not see them,
# so the first interpreter that can import them is used.
RUN='import sys; sys.path.insert(0, "/usr/share/camaloop"); from camaloop.app import main; sys.exit(main())'
for py in /usr/bin/python3 python3; do
    command -v "$py" >/dev/null 2>&1 || continue
    if "$py" -c 'import PyQt5, cv2, numpy' >/dev/null 2>&1; then
        exec "$py" -c "$RUN" "$@"
    fi
done
exec /usr/bin/python3 -c "$RUN" "$@"
EOF
chmod 755 "$STAGE/usr/bin/$PKG"

install -m 644 "$HERE/$PKG.desktop" "$STAGE/usr/share/applications/$PKG.desktop"
install -m 644 "$HERE/$PKG.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/$PKG.svg"
install -m 644 "$ROOT/README.md" "$STAGE/usr/share/doc/$PKG/README.md"
install -m 644 "$ROOT/CHANGELOG.md" "$STAGE/usr/share/doc/$PKG/CHANGELOG.md"
install -m 644 "$ROOT/NOTICE" "$STAGE/usr/share/doc/$PKG/NOTICE"

# Debian wants a machine-readable copyright file rather than a bare licence,
# and it wants the GPL itself referenced from /usr/share/common-licenses
# instead of copied into every package.
cat > "$STAGE/usr/share/doc/$PKG/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: camaloop
Upstream-Contact: Khavish Auckaloo <301292531+a-khavish@users.noreply.github.com>
Source: https://github.com/a-khavish/camaloop

Files: *
Copyright: 2026 Khavish Auckaloo
License: GPL-3

Files: camaloop/data/haarcascade_frontalface_default.xml
       camaloop/data/haarcascade_eye.xml
Copyright: 2000 Intel Corporation
License: Intel-OpenCV
Comment: OpenCV's face and eye detectors, copied unchanged from the OpenCV
 distribution. Each file carries the Intel License Agreement verbatim in its
 own header, which is where the full text of that licence lives.

License: GPL-3
 This program is free software: you may redistribute it and/or modify it
 under the terms of version 3 of the GNU General Public License as published
 by the Free Software Foundation.
 .
 This program is distributed in the hope that it will be useful, but WITHOUT
 ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 more details.
 .
 On Debian systems the full text of version 3 of the GNU General Public
 License can be found in /usr/share/common-licenses/GPL-3.

License: Intel-OpenCV
 Redistribution and use in source and binary forms, with or without
 modification, are permitted provided that redistributions retain the
 copyright notice, the list of conditions and the disclaimer; that
 redistributions in binary form reproduce them in the accompanying
 materials; and that neither the name of Intel Corporation nor the names of
 its contributors are used to endorse or promote derived products without
 specific prior written permission.
 .
 This software is provided by the copyright holders and contributors "as is"
 and any express or implied warranties, including the implied warranties of
 merchantability and fitness for a particular purpose, are disclaimed. The
 full text accompanies each file in camaloop/data/.
EOF

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: video
Priority: optional
Architecture: all
Maintainer: $MAINTAINER
Depends: python3 (>= 3.8), python3-pyqt5, python3-opencv, python3-numpy
Recommends: v4l2loopback-dkms, v4l2loopback-utils, polkitd | policykit-1,
 python3-pil, ffmpeg, qtwayland5, python3-gi, xdg-desktop-portal
Suggests: python3-mediapipe, gstreamer1.0-pipewire, grim,
 xdg-desktop-portal-gnome | xdg-desktop-portal-kde | xdg-desktop-portal-wlr
Homepage: https://github.com/a-khavish/camaloop
Description: Live camera effects and virtual camera management
 Camaloop applies effects to a webcam as it runs and publishes the result
 as a virtual camera, so browsers, video calls and recorders see the edited
 picture as an ordinary device.
 .
 It includes framing and zoom, exposure and colour correction, green screen
 and background replacement, face hiding, stylising filters, and text and
 logo overlays. Virtual cameras can be created, renamed, resized and removed
 from the interface without touching a terminal.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
    fi
    echo "Camaloop installed. Two things worth doing:"
    echo "  - add yourself to the video group:  sudo usermod -aG video \$USER"
    echo "  - install the virtual camera driver: sudo apt install v4l2loopback-dkms"
fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    rm -rf /usr/share/camaloop
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
fi
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postrm"

OUT="$ROOT/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null

echo
echo "Built $OUT"
dpkg-deb --info "$OUT" | sed -n '1,12p'
echo
echo "Install it with:"
echo "    sudo apt install $OUT"
