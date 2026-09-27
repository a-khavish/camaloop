# Camaloop - one camera in, any camera out.
# Copyright (c) 2026 Khavish Auckaloo.
#
# This program is free software: you may redistribute it and/or modify it
# under the terms of version 3 of the GNU General Public License as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY - without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.

"""The installer's shell logic, exercised with stand-in package managers.

Every bug this script has had was in exactly these functions: a package name
a release had dropped taking the whole install down with it, and a pipeline
returning 141 because `grep -q` closed the pipe early. Both are checked here.
"""

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"


def extract(*names):
    """Pull named shell functions out of setup.sh so they can be run alone."""
    text = SETUP.read_text()
    out = []
    for name in names:
        match = re.search(rf"^{re.escape(name)}\(\) \{{\n(.*?)^\}}\n",
                          text, re.S | re.M)
        if match is None:
            raise AssertionError(f"{name}() is not in setup.sh any more")
        out.append(f"{name}() {{\n{match.group(1)}}}\n")
    return "".join(out)


def run(script, stubs=None, env=None):
    """Run a shell snippet with fake commands on the PATH."""
    with tempfile.TemporaryDirectory() as folder:
        binaries = Path(folder) / "bin"
        binaries.mkdir()
        for name, body in (stubs or {}).items():
            path = binaries / name
            path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
            path.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = f"{binaries}:{environment['PATH']}"
        environment.update(env or {})
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=environment, timeout=30)


class TheScriptItself(unittest.TestCase):
    def test_it_parses(self):
        done = subprocess.run(["bash", "-n", str(SETUP)], capture_output=True,
                              text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_install_and_uninstall_parse(self):
        for name in ("install.sh", "packaging/build-deb.sh"):
            done = subprocess.run(["bash", "-n", str(ROOT / name)],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, f"{name}: {done.stderr}")

    def test_it_knows_the_package_managers(self):
        text = SETUP.read_text()
        for manager in ("apt-get", "dnf", "pacman", "zypper", "apk",
                        "xbps-install", "eopkg"):
            self.assertIn(manager, text, manager)

    def test_no_pipe_into_a_short_reading_command(self):
        """`set -o pipefail` plus `grep -q` gives exit 141 on a long input.

        That combination once poisoned six separate checks in this script,
        including the one that decides whether you are in the video group.
        """
        text = SETUP.read_text()
        self.assertIn("pipefail", text, "the script no longer sets pipefail")
        offenders = [
            f"line {number}: {line.strip()}"
            for number, line in enumerate(text.splitlines(), 1)
            if "|" in line and re.search(r"\|\s*(grep\s+-[a-zA-Z]*q|head\b)", line)
        ]
        self.assertEqual(offenders, [])


class LookingUpPackages(unittest.TestCase):
    PREAMBLE = "set -euo pipefail\nSUDO=''\n"

    def check(self, manager, stubs, package):
        script = (self.PREAMBLE + f'MANAGER="{manager}"\n' + extract("pkg_exists")
                  + f'if pkg_exists "{package}"; then echo YES; else echo NO; fi')
        return run(script, stubs).stdout.strip()

    def test_apt_sees_a_package_that_exists(self):
        stub = {"apt-cache": 'echo "ffmpeg:"; echo "  Installed: (none)";'
                             ' echo "  Candidate: 7:6.1-3"'}
        self.assertEqual(self.check("apt-get", stub, "ffmpeg"), "YES")

    def test_apt_sees_a_package_that_was_dropped(self):
        # Exactly what Ubuntu 25.04 does for policykit-1.
        stub = {"apt-cache": 'echo "policykit-1:"; echo "  Installed: (none)";'
                             ' echo "  Candidate: (none)"'}
        self.assertEqual(self.check("apt-get", stub, "policykit-1"), "NO")

    def test_apt_sees_a_package_that_is_not_there_at_all(self):
        stub = {"apt-cache": 'echo "N: Unable to locate package $2" >&2; exit 0'}
        self.assertEqual(self.check("apt-get", stub, "nonsense"), "NO")

    def test_pacman(self):
        self.assertEqual(
            self.check("pacman", {"pacman": 'exit 0'}, "qt5-wayland"), "YES")
        self.assertEqual(
            self.check("pacman", {"pacman": 'exit 1'}, "nonsense"), "NO")

    def test_apk(self):
        self.assertEqual(
            self.check("apk", {"apk": 'echo "py3-qt5-5.15.10-r0"'}, "py3-qt5"),
            "YES")
        self.assertEqual(self.check("apk", {"apk": 'true'}, "nonsense"), "NO")

    def test_xbps(self):
        stub_yes = {"xbps-query": 'echo "python3-PyQt5-5.15.10_1"'}
        self.assertEqual(self.check("xbps-install", stub_yes, "python3-PyQt5"),
                         "YES")
        self.assertEqual(self.check("xbps-install", {"xbps-query": 'true'},
                                    "nonsense"), "NO")

    def test_eopkg(self):
        self.assertEqual(
            self.check("eopkg", {"eopkg": 'echo "Name: pyqt5, version: 5.15"'},
                       "pyqt5"), "YES")
        self.assertEqual(
            self.check("eopkg", {"eopkg": 'echo "Package nonsense not found"'},
                       "nonsense"), "NO")

    def test_an_unknown_manager_says_no(self):
        self.assertEqual(self.check("skipped", {}, "anything"), "NO")


class FilteringPackageLists(unittest.TestCase):
    def filtered(self, wanted, available):
        stub = {"apt-cache": (
            'for name in ' + " ".join(available) + '; do\n'
            '  if [ "$2" = "$name" ]; then echo "  Candidate: 1.0"; exit 0; fi\n'
            'done\n'
            'echo "  Candidate: (none)"\n'
        )}
        script = ("set -euo pipefail\nSUDO=''\nMANAGER='apt-get'\n"
                  + extract("pkg_exists", "pkg_filter")
                  + f'pkg_filter {wanted}')
        return run(script, stub).stdout.strip()

    def test_one_missing_name_does_not_lose_the_others(self):
        # The bug that made a whole install fail: policykit-1 was on the same
        # apt line as the libraries, so its absence took them all with it.
        got = self.filtered("python3-pyqt5 policykit-1 python3-opencv",
                            ["python3-pyqt5", "python3-opencv"])
        self.assertEqual(got.split(), ["python3-pyqt5", "python3-opencv"])

    def test_everything_available(self):
        got = self.filtered("a b c", ["a", "b", "c"])
        self.assertEqual(got.split(), ["a", "b", "c"])

    def test_nothing_available(self):
        self.assertEqual(self.filtered("a b", []), "")


class CarryingTheInterpreterThrough(unittest.TestCase):
    """setup.sh works out which python has the libraries; install.sh must
    use that one and not pick its own."""

    def test_the_name_matches_at_both_ends(self):
        exported = re.search(r'export (\w+)="\$PYTHON"', SETUP.read_text())
        self.assertIsNotNone(exported, "setup.sh no longer exports it")
        name = exported.group(1)
        self.assertIn(f"${{{name}:-}}", (ROOT / "install.sh").read_text())


if __name__ == "__main__":
    unittest.main()
