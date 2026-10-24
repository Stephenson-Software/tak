"""BaseUserInterface.setPicture: an optional, sticky picture above every screen.

Each front-end gets its own tests here: the console shows nothing, the
server-backed web front-end names the picture on every screen and serves its
bytes at /picture/<key>, and the Pyodide front-end has the Worker send each
picture's bytes once, before the first screen that names it.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

from tak import Prompt
from tak.ui.console import ConsoleUserInterface
from tak.ui.factory import createUserInterface
from tak.ui.pyodide import PyodideUserInterface, SharedArrayBufferBridge
from tak.ui.uitype import UIType
from tak.ui.web import WebUserInterface
from conftest import makeHeader

PNG = b"\x89PNG\r\n\x1a\n" + b"not really a picture"


def makeWebUI(picturesDir, start_server=False):
    return WebUserInterface(
        Prompt(),
        makeHeader(["Day 1"]),
        title="Overwinter",
        port=0,
        start_server=start_server,
        endedScreenTimeoutSeconds=0.1,
        picturesDir=str(picturesDir),
    )


class PictureJs:
    """Adds the Worker's sendPicture to conftest's fake js module."""

    def __init__(self, fakeJs):
        self.fakeJs = fakeJs
        self.sentPictures = []
        fakeJs.sendPicture = self.sendPicture

    def sendPicture(self, key):
        # Recorded in the same list as screens, so ordering can be checked.
        self.sentPictures.append(key)
        self.fakeJs.posted.append(json.dumps({"type": "picture", "key": key}))


def makePyodideUI(js):
    return PyodideUserInterface(
        Prompt(),
        makeHeader(["Day 1"]),
        title="Overwinter",
        bridge=SharedArrayBufferBridge(js=js),
        pollIntervalSeconds=0,
    )


# --- the contract -----------------------------------------------------------


def test_no_picture_until_one_is_set(prompt):
    ui = ConsoleUserInterface(prompt)
    assert ui.picture() is None


def test_setPicture_is_sticky_until_cleared(prompt):
    ui = ConsoleUserInterface(prompt)
    ui.setPicture("strip", "The airstrip under snow, one lamp burning.")
    assert ui.picture() == {
        "key": "strip",
        "alt": "The airstrip under snow, one lamp burning.",
    }
    ui.setPicture(None)
    assert ui.picture() is None


@pytest.mark.parametrize("key", ["../secret", "Strip", "strip.png", "", "a/b", 7])
def test_setPicture_refuses_a_key_that_is_not_a_bare_file_name(prompt, key):
    ui = ConsoleUserInterface(prompt)
    with pytest.raises(ValueError, match="picture key"):
        ui.setPicture(key, "alt text")


@pytest.mark.parametrize("alt", [None, "", "   "])
def test_setPicture_requires_alt_text(prompt, alt):
    ui = ConsoleUserInterface(prompt)
    with pytest.raises(ValueError, match="alt text"):
        ui.setPicture("strip", alt)


def test_console_prints_nothing_for_a_picture(prompt, capsys):
    ui = ConsoleUserInterface(prompt)
    ui.setPicture("strip", "The airstrip under snow.")
    assert capsys.readouterr().out == ""


# --- WebUserInterface -------------------------------------------------------


def test_every_web_screen_names_the_picture_but_carries_no_bytes(tmp_path):
    ui = makeWebUI(tmp_path)
    ui.setPicture("galley", "The galley: a stove, a table, four chairs.")
    ui.submit_input("")
    ui.showDialogue("The stove ticks.")
    screen = ui.get_state()["screen"]
    assert screen["picture"] == {
        "key": "galley",
        "alt": "The galley: a stove, a table, four chairs.",
    }
    assert set(screen["picture"]) == {"key", "alt"}


def test_a_screen_without_a_picture_has_no_picture_field(tmp_path):
    ui = makeWebUI(tmp_path)
    ui.submit_input("")
    ui.showDialogue("Dark.")
    assert "picture" not in ui.get_state()["screen"]


def test_the_ended_screen_carries_no_picture(tmp_path):
    ui = makeWebUI(tmp_path)
    ui.setPicture("galley", "The galley.")
    ui.cleanup()
    assert ui.get_state()["screen"] == {"type": "ended"}


def test_pictureBytes_reads_only_bare_keys_inside_the_directory(tmp_path):
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    (pictures / "strip.png").write_bytes(PNG)
    (tmp_path / "secret.png").write_bytes(b"secret")
    ui = makeWebUI(pictures)
    assert ui.pictureBytes("strip") == PNG
    assert ui.pictureBytes("missing") is None
    assert ui.pictureBytes("../secret") is None
    assert ui.pictureBytes("%2e%2e/secret") is None


def test_server_serves_a_picture_as_png_and_404s_anything_else(tmp_path):
    (tmp_path / "strip.png").write_bytes(PNG)
    ui = makeWebUI(tmp_path, start_server=True)
    try:
        base = "http://127.0.0.1:%d" % ui.address[1]
        response = urllib.request.urlopen(base + "/picture/strip", timeout=2)
        assert response.headers["Content-Type"] == "image/png"
        assert "max-age" in response.headers["Cache-Control"]
        assert response.read() == PNG
        for path in ("/picture/missing", "/picture/..%2fstrip", "/picture/"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(base + path, timeout=2)
            assert err.value.code == 404
    finally:
        ui.cleanup()


def test_the_page_tells_the_client_where_pictures_are_served(tmp_path):
    assert '"/picture/" + encodeURIComponent(key)' in makeWebUI(tmp_path).page()


def test_factory_passes_the_pictures_directory_to_the_web_front_end(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TAK_WEB_PORT", "0")
    ui = createUserInterface(UIType.WEB, Prompt(), picturesDir=str(tmp_path))
    try:
        assert ui.picturesDir == str(tmp_path)
    finally:
        ui.cleanup()


def test_web_front_end_defaults_to_a_pictures_directory(tmp_path):
    ui = WebUserInterface(Prompt(), start_server=False)
    assert ui.picturesDir == "pictures"
    assert os.path.join(ui.picturesDir, "strip.png") == os.path.join(
        "pictures", "strip.png"
    )


# --- PyodideUserInterface ---------------------------------------------------


def test_pyodide_sends_a_pictures_bytes_once_before_the_first_screen_naming_it(
    fakeJs,
):
    pictureJs = PictureJs(fakeJs)
    ui = makePyodideUI(fakeJs)
    ui.setPicture("radio", "The radio room: a desk, a set, a log book.")
    for _ in range(3):
        fakeJs.writePlayerInput("")
        ui.showDialogue("Static.")
    assert pictureJs.sentPictures == ["radio"]
    kinds = [json.loads(message)["type"] for message in fakeJs.posted]
    assert kinds == ["picture", "screen", "screen", "screen"]
    screen = json.loads(fakeJs.posted[-1])["screen"]
    assert screen["picture"]["key"] == "radio"


def test_pyodide_sends_each_new_picture_and_never_resends_an_old_one(fakeJs):
    pictureJs = PictureJs(fakeJs)
    ui = makePyodideUI(fakeJs)
    for key in ("radio", "shed", "radio", "shed"):
        ui.setPicture(key, "A room.")
        fakeJs.writePlayerInput("")
        ui.showDialogue("...")
    assert pictureJs.sentPictures == ["radio", "shed"]


def test_pyodide_plays_on_when_the_worker_cannot_send_pictures(fakeJs):
    # An older game-worker.js installs no sendPicture.
    ui = makePyodideUI(fakeJs)
    ui.setPicture("radio", "The radio room.")
    fakeJs.writePlayerInput("1")
    assert ui.showOptions("The radio room.", ["Listen"]) == "1"
    assert json.loads(fakeJs.posted[-1])["screen"]["picture"]["key"] == "radio"
