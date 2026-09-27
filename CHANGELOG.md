# Changelog

## 1.0.0

The first release.

### The camera

- Opens any camera the system can see - built in, USB, or a capture card -
  and writes the finished picture into a virtual camera that browsers, video
  calls and recorders pick up as though it were a second webcam.
- Other sources too: a network camera over RTSP or HTTP, a video file on a
  loop, a still image, a test pattern, and the screen or a window.
- Photos and video recorded from inside the app, at the output size, with
  every effect already baked in.

### Effects

Thirty-two of them, in the order they are applied:

- **Camera** - framing (mirror, flip, quarter turns, fine angle), crop to any
  shape, zoom and reframe.
- **Background** - green screen, background replacement without a green
  screen, hide faces, cover an area.
- **Face** - face warp.
- **Colour** - exposure, colour, skin smoothing, twelve colour grades, looks,
  two-tone.
- **Texture** - softness and detail, glow, light and flare, motion trails,
  depth blur, stylise, comic dots, glitch, terminal, mirror and kaleidoscope,
  text picture, vignette, film and tube.
- **Overlay** - text with `{time}`, `{date}` and `{fps}`, a picture or video
  on top, falling things.
- **Output** - output size, frame and corners.

Nothing exceeds the frame budget for 30 fps at 720p, and a test enforces it.

### Face

- Face warp's Bulge and Pinch were the wrong way round: the warp maps each
  point to where it should be read from, so the arithmetic runs opposite to
  the name, and the sign was wrong. Bulge shrank the face and Pinch swelled
  it. A test now measures the direction rather than trusting it.

### Face detection

- Hide faces and Face warp need OpenCV's face and eye detectors, and those
  are shipped with the app as well as looked for in every place a
  distribution might keep them. On Debian and Ubuntu the OpenCV Python
  bindings and the detection data are separate packages and nothing links
  them, so a normal install can leave OpenCV working with no face detection
  at all - and anything that looks for a face would then hand back an
  unchanged picture without a word.
- If the data really cannot be found, those two effects grey themselves out
  and name the command to install it.
- `opencv-data` and its equivalents were added to what the installer puts in.

### Virtual cameras

- Create, rename, set a frame rate and a fixed size, give a standby image,
  remove, and keep across reboots - none of it in a terminal.
- Both v4l2loopback back-ends: adding one camera at a time on 0.13 and newer,
  and a driver reload on anything older.
- Kept cameras are written for systemd, OpenRC, runit and sysvinit alike.

### Screen capture

- X11 through ffmpeg's grabber: the whole screen or any rectangle.
- Wayland through the xdg-desktop-portal screen cast interface and PipeWire,
  so the desktop runs its own picker and a screen or a single window can be
  shared.
- `grim` as a fallback on wlroots compositors with no portal, and XWayland as
  a last resort. Whichever is used is reported before capture starts.

### Installing

- One command, `bash setup.sh`, on apt, dnf, pacman, zypper, apk, xbps and
  eopkg. Every package name is checked against the archive first, so one name
  a release has dropped cannot stop the rest from installing.
- A `.deb` for Debian and Ubuntu.
- `camaloop --check` reports what works on any given machine.

### The window

- Fits whatever screen it is on. The rows of controls along the top and the
  bottom of Studio fold onto a second line when the window is narrow instead
  of forcing it wider than the display, and long text sits in scroll areas
  rather than demanding a window taller than the screen.
- "Keep the window maximised" asks the window manager to maximise and puts
  the window back if something un-maximises it. It does not freeze the
  window at a size, which is a request the window manager cannot honour and
  which leaves the bottom of the window below the edge of the screen.
- Tested from 1280x720 up to 2560x1440.

### Testing

- 219 tests covering V4L2, the effects, face detection, screen capture,
  settings, presets, the virtual camera bookkeeping, the capture thread, the
  installer's shell logic, the interface and the layout. The interface tests
  build every tab and every control with no display attached; the layout
  tests resize the window to each common screen size, check the resize
  actually took effect, and then check that no control has ended up outside
  the window.
- Tests that will not let the documentation fall behind: every effect, every
  effect group must appear in the in-app documentation.
- A test that no effect may write over the frame it was handed, and one that
  no effect may exceed its frame budget.

### Licence

- Released under the GNU General Public License, version 3. Camaloop is built
  on PyQt5, which Riverbank Computing licenses under the GPL or a commercial
  licence, so the GPL is the licence that fits. `NOTICE` records the
  third-party content: the two OpenCV Haar cascades bundled in
  `camaloop/data/`, which keep their own Intel licence notice, and every
  runtime dependency with its licence.

### Dependencies

- OpenCV 4 is required, not OpenCV 5: version 5 removed `CascadeClassifier`
  and stopped shipping the detection data, which is what every face effect
  is built on. The requirement is `opencv-python>=4.5,<5`, so a fresh
  install gets a working one. On version 5 the face effects grey themselves
  out and name the version as the reason instead of finding no faces in
  silence, and their tests skip rather than fail.
