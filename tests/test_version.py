"""The version has three homes; a release that moves one must move all of them.

pyproject.toml and tak.__version__ are what pip and a running game report, and
the README's install line is what a stranger copies. Release PRs once bumped the
first two and left the README pinned to an old tag, so these tests fail CI on a
release PR until the pin follows.
"""

import os
import re

import tak

ROOT = os.path.join(os.path.dirname(__file__), "..")
PIN = re.compile(r"git\+https://github\.com/Stephenson-Software/tak@v(\S+?)\"")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def pyprojectVersion():
    match = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.MULTILINE)
    assert match, "pyproject.toml has no [project] version line"
    return match.group(1)


def readmePin():
    pins = PIN.findall(read("README.md"))
    message = "README.md should have exactly one install line pinning a tag"
    assert len(pins) == 1, message
    return pins[0]


def releaseTuple(version):
    return tuple(int(part) for part in version.split(".")[:3])


def test_pyproject_and_dunder_version_agree():
    message = "src/tak/__init__.py __version__ and pyproject.toml version differ"
    assert tak.__version__ == pyprojectVersion(), message


def test_readme_pins_the_release_on_a_stable_version():
    version = pyprojectVersion()
    if ".dev" in version:
        return
    assert readmePin() == version, (
        "README.md's install line must pin v%s in the release that sets that version"
        % version
    )


def test_readme_pin_is_not_ahead_of_a_dev_version():
    version = pyprojectVersion()
    if ".dev" not in version:
        return
    pin = readmePin()
    message = "README.md pins v%s, which is not released yet under %s" % (pin, version)
    assert releaseTuple(pin) < releaseTuple(version), message
