import json

import pytest

from tak import Prompt
from tak.ui import BaseUserInterface, web as webModule
from tak.ui.factory import createUserInterface
from tak.ui.pyodide import PyodideUserInterface, SharedArrayBufferBridge
from tak.ui.uitype import UIType
from conftest import RING_SIZE, makeHeader


def makePyodideUI(js):
    return PyodideUserInterface(
        Prompt(),
        makeHeader(["Day 1"]),
        title="Tidewater",
        bridge=SharedArrayBufferBridge(js=js),
        # Nothing in these tests ever has to wait for input that isn't already
        # in the ring, so the poll interval only affects the failure case.
        pollIntervalSeconds=0,
    )


def lastScreen(js):
    return json.loads(js.posted[-1])["screen"]


def test_pyodide_ui_implements_interface_and_starts_no_server(fakeJs):
    ui = makePyodideUI(fakeJs)
    assert isinstance(ui, BaseUserInterface)
    assert ui.address is None


def test_screens_are_posted_to_the_main_thread_with_the_header(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("1")
    assert ui.showOptions("The Docks", ["Fish", "Leave"]) == "1"
    frame = json.loads(fakeJs.posted[-1])
    assert frame["type"] == "screen"
    assert frame["screen"]["options"] == ["Fish", "Leave"]
    assert frame["screen"]["header"]["chips"] == [{"text": "Day 1", "class": ""}]


def test_present_still_updates_the_state_snapshot(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("")
    ui.showDialogue("Hello.")
    assert ui.get_state()["screen"] == {"type": "dialogue", "text": "Hello."}
    assert ui.get_state()["version"] == 1


def test_showOptions_ignores_invalid_input_then_accepts_valid(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("9")
    fakeJs.writePlayerInput("2")
    assert ui.showOptions("Pick", ["A", "B"]) == "2"


def test_promptForText_round_trips_text_with_newlines(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("line one\nline two")
    assert ui.promptForText("Name?") == "line one\nline two"


def test_promptForNumber_parses_and_marks_the_screen_numeric(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("3.5")
    assert ui.promptForNumber("How many?") == 3.5
    assert lastScreen(fakeJs)["numeric"] is True


def test_non_ascii_input_survives_the_ring(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("Ægir's Boat ☠")
    assert ui.promptForText("Name?") == "Ægir's Boat ☠"


def test_messages_that_are_not_input_are_ignored(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs._writeToRing("this is not json")
    fakeJs._writeToRing(json.dumps({"type": "other", "value": "nope"}))
    fakeJs.writePlayerInput("real")
    assert ui.promptForText("?") == "real"


def test_submit_input_still_works_alongside_the_ring(fakeJs):
    ui = makePyodideUI(fakeJs)
    ui.submit_input("queued")
    assert ui.promptForText("?") == "queued"


def test_ring_wraps_around_without_losing_a_message(fakeJs):
    ui = makePyodideUI(fakeJs)
    fakeJs.sabMeta[0] = RING_SIZE - 4
    fakeJs.sabMeta[1] = RING_SIZE - 4
    fakeJs.writePlayerInput("Harbourmaster")
    assert ui.promptForText("Name?") == "Harbourmaster"


def test_the_front_end_works_with_no_assets_on_disk(fakeJs, monkeypatch):
    """The Worker's filesystem has the game but the page assets reach the
    browser over HTTP; nothing on this front-end's path may read them."""
    import tak.web

    monkeypatch.setattr(tak.web, "ASSET_DIRECTORY", "/no/such/directory")
    webModule._clientAssetCache.clear()
    ui = makePyodideUI(fakeJs)
    fakeJs.writePlayerInput("1")
    assert ui.showOptions("The Docks", ["Fish", "Leave"]) == "1"
    assert ui._pageCache is None


def test_cleanup_posts_the_ended_screen(fakeJs):
    ui = makePyodideUI(fakeJs)
    ui.cleanup()
    assert lastScreen(fakeJs) == {"type": "ended"}


def test_bridge_outside_a_browser_explains_itself(noJs):
    with pytest.raises(RuntimeError) as error:
        SharedArrayBufferBridge()
    message = str(error.value)
    assert "browser" in message and "UIType.CONSOLE" in message


def test_bridge_without_worker_globals_names_what_is_missing(fakeJs):
    fakeJs.sendToMain = None
    with pytest.raises(RuntimeError) as error:
        SharedArrayBufferBridge(js=fakeJs)
    assert "sendToMain" in str(error.value)


def test_factory_creates_the_pyodide_front_end(fakeJs):
    ui = createUserInterface(UIType.PYODIDE, Prompt(), title="Tidewater")
    assert isinstance(ui, PyodideUserInterface)
    assert ui.title == "Tidewater"
