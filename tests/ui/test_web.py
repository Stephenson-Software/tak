import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from tak import Prompt
from tak.ui import web as webModule
from tak.ui.web import WebUserInterface, resolveAddressFromEnvironment
from tak.ui.factory import createUserInterface
from tak.ui.uitype import UIType
from conftest import makeHeader


def makeWebUI(
    start_server=False, port=0, endedScreenTimeoutSeconds=0.1, chips=None, **kwargs
):
    # No browser polls these servers, so cleanup()'s wait for the ended screen
    # to be collected always runs to its timeout; the production default (two
    # seconds) would be paid by every test that starts one.
    return WebUserInterface(
        Prompt(),
        makeHeader(chips, title="Tidewater - Loop 3"),
        title=kwargs.pop("title", "Tidewater"),
        port=port,
        start_server=start_server,
        endedScreenTimeoutSeconds=endedScreenTimeoutSeconds,
        **kwargs,
    )


def runInThread(fn):
    box = {}
    thread = threading.Thread(target=lambda: box.__setitem__("result", fn()))
    thread.start()
    return thread, box


def waitForScreen(ui, screenType, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ui.get_state()["screen"].get("type") == screenType:
            return
        time.sleep(0.01)
    raise AssertionError("screen %r was never presented" % screenType)


def test_options_screen_carries_the_normalized_header():
    ui = makeWebUI(chips=["Day 3", {"text": "Energy: 4/10", "class": "low"}])
    ui.currentPrompt.text = "Well?"
    thread, box = runInThread(lambda: ui.showOptions("Docks", ["Fish", "Leave"]))
    waitForScreen(ui, "options")
    screen = ui.get_state()["screen"]
    assert screen["header"] == {
        "title": "Tidewater - Loop 3",
        "chips": [
            {"text": "Day 3", "class": ""},
            {"text": "Energy: 4/10", "class": "low"},
        ],
    }
    assert screen["descriptor"] == "Docks" and screen["prompt"] == "Well?"
    assert screen["options"] == ["Fish", "Leave"] and screen["unavailable"] == [
        None,
        None,
    ]
    ui.submit_input("2")
    thread.join(timeout=2)
    assert box["result"] == "2"


def test_showOptions_ignores_invalid_then_accepts_valid():
    ui = makeWebUI()
    thread, box = runInThread(lambda: ui.showOptions("Pick", ["A", "B"]))
    waitForScreen(ui, "options")
    ui.submit_input("banana")
    ui.submit_input("7")
    ui.submit_input("1")
    thread.join(timeout=2)
    assert box["result"] == "1"


def test_showOptions_publishes_the_reason_each_option_is_unavailable_and_refuses_it():
    ui = makeWebUI()
    thread, box = runInThread(
        lambda: ui.showOptions("Docks", ["Fish", "Leave"], {1: "too tired"})
    )
    waitForScreen(ui, "options")
    assert ui.get_state()["screen"]["unavailable"] == ["too tired", None]
    ui.submit_input("1")  # refused
    ui.submit_input("2")
    thread.join(timeout=2)
    assert box["result"] == "2"


def test_promptForText_round_trips_text():
    ui = makeWebUI()
    thread, box = runInThread(lambda: ui.promptForText("Name?"))
    waitForScreen(ui, "prompt")
    assert ui.get_state()["screen"] == {"type": "prompt", "text": "Name?"}
    ui.submit_input("Harbourmaster")
    thread.join(timeout=2)
    assert box["result"] == "Harbourmaster"


def test_promptForNumber_marks_screen_numeric_and_parses():
    ui = makeWebUI()
    thread, box = runInThread(lambda: ui.promptForNumber("How many?"))
    waitForScreen(ui, "prompt")
    assert ui.get_state()["screen"]["numeric"] is True
    ui.submit_input("12")
    thread.join(timeout=2)
    assert box["result"] == 12.0

    thread, box = runInThread(lambda: ui.promptForNumber("How many?"))
    waitForScreen(ui, "prompt")
    ui.submit_input("no")
    thread.join(timeout=2)
    assert box["result"] is None


def test_showDialogue_waits_then_resets_prompt():
    ui = makeWebUI()
    ui.currentPrompt.text = "Custom"
    thread, box = runInThread(lambda: ui.showDialogue("Hello."))
    waitForScreen(ui, "dialogue")
    assert ui.get_state()["screen"]["text"] == "Hello."
    ui.submit_input("")
    thread.join(timeout=2)
    assert ui.currentPrompt.text == Prompt.DEFAULT


def test_timedKeyPress_returns_elapsed_seconds():
    ui = makeWebUI()
    thread, box = runInThread(lambda: ui.timedKeyPress("Now!"))
    waitForScreen(ui, "timed")
    time.sleep(0.05)
    ui.submit_input("")
    thread.join(timeout=2)
    assert box["result"] >= 0.04


def test_showBusy_presents_the_message_without_consuming_input(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    ui = makeWebUI()
    ui.submit_input("leftover")
    ui.showBusy("Fishing...", seconds=0.1)
    assert ui.get_state()["screen"] == {"type": "busy", "message": "Fishing..."}
    assert ui._inputQueue.get_nowait() == "leftover"


def test_page_inlines_the_client_and_names_the_game():
    ui = makeWebUI(tagline="a day that repeats", tip="Press keys.")
    page = ui.page()
    assert "<title>Tidewater</title>" in page
    assert "a day that repeats" in page and "Press keys." in page
    assert "window.TakClient" in page
    assert 'name="viewport"' in page
    assert "@media (max-width: 600px)" in page
    assert 'state.screen.type === "ended"' in page  # the poll loop stops


def test_page_escapes_html_in_the_title():
    assert "&lt;b&gt;" in makeWebUI(title="<b>").page()


def test_http_server_serves_and_accepts_input():
    ui = makeWebUI(start_server=True, port=0)
    try:
        host, port = ui.address
        base = "http://127.0.0.1:%d" % port

        page = urllib.request.urlopen(base + "/", timeout=2).read().decode("utf-8")
        assert "Tidewater" in page

        state = json.loads(urllib.request.urlopen(base + "/state", timeout=2).read())
        assert "version" in state and "screen" in state

        thread, box = runInThread(lambda: ui.showOptions("Pick", ["A", "B"]))
        waitForScreen(ui, "options")
        request = urllib.request.Request(
            base + "/input",
            data=json.dumps({"value": "1"}).encode("utf-8"),
            method="POST",
        )
        urllib.request.urlopen(request, timeout=2).read()
        thread.join(timeout=2)
        assert box["result"] == "1"
    finally:
        ui.cleanup()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_http_server_returns_404_for_unknown_paths(method):
    ui = makeWebUI(start_server=True, port=0)
    try:
        host, port = ui.address
        request = urllib.request.Request(
            "http://127.0.0.1:%d/nonexistent" % port,
            data=b"{}" if method == "POST" else None,
            method=method,
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        assert error.value.code == 404
    finally:
        ui.cleanup()


@pytest.mark.parametrize("body", [b"not json", b"[1,2]", b""])
def test_http_server_post_input_with_malformed_json_defaults_to_empty_value(body):
    ui = makeWebUI(start_server=True, port=0)
    try:
        host, port = ui.address
        thread, box = runInThread(lambda: ui.promptForText("Name?"))
        waitForScreen(ui, "prompt")
        request = urllib.request.Request(
            "http://127.0.0.1:%d/input" % port, data=body, method="POST"
        )
        urllib.request.urlopen(request, timeout=2).read()
        thread.join(timeout=2)
        assert box["result"] == ""
    finally:
        ui.cleanup()


def test_cleanup_publishes_the_ended_screen():
    ui = makeWebUI()
    ui.cleanup()
    assert ui.get_state()["screen"] == {"type": "ended"}


def test_cleanup_holds_the_server_open_until_the_ended_screen_is_fetched():
    ui = makeWebUI(start_server=True, port=0, endedScreenTimeoutSeconds=2.0)
    host, port = ui.address
    base = "http://127.0.0.1:%d" % port

    thread, box = runInThread(ui.cleanup)
    try:
        waitForScreen(ui, "ended")
        assert thread.is_alive()
        state = json.loads(urllib.request.urlopen(base + "/state", timeout=2).read())
        assert state["screen"] == {"type": "ended"}
    finally:
        thread.join(timeout=3)

    assert not thread.is_alive()
    assert ui.address is None


def test_cleanup_stops_waiting_when_nothing_is_listening():
    ui = makeWebUI(start_server=True, port=0, endedScreenTimeoutSeconds=0.2)
    startTime = time.time()
    ui.cleanup()
    elapsed = time.time() - startTime
    assert 0.2 <= elapsed < 2.0
    assert ui.address is None


def test_record_state_delivered_keeps_the_highest_version_seen():
    ui = makeWebUI()
    ui._present({"type": "dialogue", "text": "x"})
    version = ui.get_state()["version"]
    ui.record_state_delivered(version)
    ui.record_state_delivered(version - 1)
    assert ui._awaitScreenDelivery(timeout=0) is True


def test_waiting_for_delivery_reports_an_uncollected_screen():
    ui = makeWebUI()
    ui.record_state_delivered(ui.get_state()["version"])
    ui._present({"type": "dialogue", "text": "x"})
    assert ui._awaitScreenDelivery(timeout=0) is False


def test_taken_port_is_explained_rather_than_traced():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError) as error:
            makeWebUI(start_server=True, port=port, envPrefix="TIDEWATER")
    finally:
        blocker.close()
    message = str(error.value)
    assert "Tidewater" in message
    assert "TIDEWATER_WEB_PORT" in message and "TIDEWATER_WEB_HOST" in message
    assert "already listening" in message


def test_bound_address_is_announced_once_the_server_is_listening(capsys):
    ui = makeWebUI(start_server=True, port=0)
    try:
        host, port = ui.address
        out = capsys.readouterr().out
        assert "Tidewater is being served at http://127.0.0.1:%d/" % port in out
    finally:
        ui.cleanup()


def test_nothing_is_announced_when_no_server_was_started(capsys):
    makeWebUI()
    assert capsys.readouterr().out == ""


def test_address_defaults_to_loopback_and_the_documented_port(monkeypatch):
    monkeypatch.delenv("TAK_WEB_HOST", raising=False)
    monkeypatch.delenv("TAK_WEB_PORT", raising=False)
    assert resolveAddressFromEnvironment() == ("127.0.0.1", 8000)


def test_address_is_taken_from_the_prefixed_environment_variables(monkeypatch):
    monkeypatch.setenv("TIDEWATER_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "9000")
    monkeypatch.setenv("TAK_WEB_PORT", "1")  # another prefix must not leak in
    assert resolveAddressFromEnvironment("TIDEWATER") == ("0.0.0.0", 9000)


def test_misspelled_port_names_the_variable_it_came_from(monkeypatch):
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "eight thousand")
    with pytest.raises(ValueError) as error:
        resolveAddressFromEnvironment("TIDEWATER")
    assert "TIDEWATER_WEB_PORT" in str(error.value)


def test_factory_reads_the_environment_for_the_web_front_end(monkeypatch):
    monkeypatch.setenv("TIDEWATER_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "0")
    ui = createUserInterface(
        UIType.WEB, Prompt(), title="Tidewater", envPrefix="TIDEWATER"
    )
    try:
        assert isinstance(ui, WebUserInterface)
        assert ui.address[0] == "127.0.0.1"
        assert ui.envPrefix == "TIDEWATER"
    finally:
        ui._endedScreenTimeoutSeconds = 0.05
        ui.cleanup()


def test_factory_rejects_an_unknown_type():
    with pytest.raises(ValueError):
        createUserInterface("teletype", Prompt())


def test_missing_asset_is_explained(monkeypatch):
    import tak.web

    monkeypatch.setattr(tak.web, "ASSET_DIRECTORY", "/no/such/directory")
    monkeypatch.setattr(webModule, "_clientAssetCache", {})
    with pytest.raises(RuntimeError) as error:
        makeWebUI().page()
    assert "client.css" in str(error.value)
