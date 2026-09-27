# Contributing

Thanks for taking a look. This is a desktop application for Linux, written in
Python with PyQt5 and OpenCV, and it has no build step - clone it and run it.

## Running from a clone

```bash
git clone https://github.com/a-khavish/camaloop.git
cd camaloop
bash setup.sh          # installs everything and runs the tests
python3 run.py         # or start it straight from the clone
```

If you already have PyQt5, OpenCV and NumPy, `python3 run.py` is enough.

## Running the tests

```bash
python3 tools/selftest.py              # every effect over a synthetic frame
python3 -m unittest discover -s tests  # the unit tests
python3 tools/smoketest.py             # starts the interface and draws it
python3 tools/verify_pipeline.py       # end to end, needs v4l2loopback
bash -n setup.sh                       # the installer parses
```

The layout checks need somewhere to put a large window, and Qt's offscreen
platform pretends the screen is 800x600, so they skip themselves when there
is no display. Give them a virtual one to run the whole matrix:

```bash
xvfb-run -a --server-args="-screen 0 2560x1440x24" \
    env QT_QPA_PLATFORM= python3 -m unittest discover -s tests
```

`verify_pipeline.py` is the only one that needs a real kernel module, so it is
the one to run on your own machine before sending a change that touches
capture or output.

## Where things live

| Path | What is in it |
| --- | --- |
| `camaloop/core/v4l2.py` | Talking to `/dev/video*` directly, no bindings |
| `camaloop/core/loopback.py` | Creating, editing and removing virtual cameras |
| `camaloop/core/effects.py` | Every effect, and the pipeline that runs them |
| `camaloop/core/faces.py` | Finding faces and keeping them steady |
| `camaloop/core/screen.py` | Using the screen as a camera, X11 and Wayland |
| `camaloop/core/engine.py` | The capture thread |
| `camaloop/ui/` | The interface |
| `tools/` | Tests and diagnostics |
| `packaging/` | Desktop entry, icon, and the `.deb` build |

## Adding an effect

Add a class to `camaloop/core/effects.py` with an `id`, a `name`, a `group`,
a `blurb` and a tuple of `Param`s, then put an instance in `EFFECTS`. The
interface builds its own controls from the parameters, so there is no
interface code to write.

Two things to keep in mind:

- Frames are BGR `uint8`, and an effect returns one of the same kind.
- Budget about 8 ms per effect at 720p. Prefer OpenCV calls to whole-frame
  NumPy arithmetic; the `_blend`, `_screen` and `_flat` helpers at the top of
  the file exist for exactly this and are several times quicker than the
  obvious float32 version.

An effect that needs to know where a face is uses `FaceTracker` from
`camaloop/core/faces.py`, and its `availability()` should return
`face_detection_ready()` so it greys itself out rather than silently doing
nothing when the detection data cannot be found.

Run `python3 tools/selftest.py` afterwards. It exercises every branch of every
choice parameter and prints how long each effect took.

## Adding a control to the interface

Anything with a fixed width or height needs a reason. A page that insists on
being larger than the screen is a page the window manager cannot fit, and the
part that does not fit goes off the bottom - `tests/test_layout.py` exists
because of exactly that. Two habits avoid it:

- Long text goes in a `QScrollArea`. A word-wrapped `QLabel` dropped straight
  into a panel asks for however many hundreds of pixels its text needs.
- A row of buttons goes in a `FlowLayout` (in `camaloop/ui/widgets.py`), which
  folds onto a second line when it runs short of room. Wrap the panel in
  `wraps()` so its height can follow from its width, and use `spacer()` where
  a plain layout would have had a stretch.

## Style

- Four spaces, lines under 88 characters.
- Comments explain *why*, not *what*. If a line is doing something surprising -
  working around a driver quirk, avoiding a slow path - say so.
- Plain English in anything a user will read. No jargon in error messages.

## Reporting a problem

Please include the output of:

```bash
python3 run.py --list-devices
python3 tools/selftest.py
uname -r && echo "$XDG_SESSION_TYPE" && echo "$XDG_CURRENT_DESKTOP"
```

That covers nearly every question anyone would otherwise have to ask you.
