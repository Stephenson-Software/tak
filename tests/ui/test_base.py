import time

import pytest

from tak import NPC, Prompt
from tak.ui import BaseUserInterface, unavailableMessage, unavailableSuffix
from tak.ui.base import normalizeHeader
from tak.ui.console import ConsoleUserInterface
from tak.ui.web import WebUserInterface
from tak.ui.pyodide import PyodideUserInterface, SharedArrayBufferBridge
from conftest import makeHeader


class ScriptedUI(BaseUserInterface):
    """A minimal front-end: answers from a script, records what it showed."""

    def __init__(self, answers, header=None):
        super().__init__(Prompt(), header)
        self.answers = list(answers)
        self.shown = []

    def lotsOfSpace(self):
        pass

    def divider(self):
        pass

    def showOptions(self, descriptor, optionList, unavailableOptions=None):
        self.shown.append(("options", descriptor, list(optionList)))
        return self.answers.pop(0)

    def showDialogue(self, text):
        self.shown.append(("dialogue", text))

    def promptForText(self, promptText):
        self.shown.append(("prompt", promptText))
        return self.answers.pop(0)

    def timedKeyPress(self, message):
        return 0.0

    def cleanup(self):
        pass


def test_base_user_interface_is_abstract():
    with pytest.raises(TypeError):
        BaseUserInterface(Prompt())


def test_front_ends_implement_the_interface(fakeJs):
    prompt = Prompt()
    assert isinstance(ConsoleUserInterface(prompt), BaseUserInterface)
    assert isinstance(WebUserInterface(prompt, start_server=False), BaseUserInterface)
    assert isinstance(
        PyodideUserInterface(prompt, bridge=SharedArrayBufferBridge(js=fakeJs)),
        BaseUserInterface,
    )


def test_header_defaults_to_empty_and_is_read_fresh_each_time():
    ui = ScriptedUI([])
    assert ui.header() == {"title": "", "chips": []}
    state = {"day": 1}
    ui = ScriptedUI([], header=lambda: {"chips": ["Day %d" % state["day"]]})
    assert ui.header()["chips"] == [{"text": "Day 1", "class": ""}]
    state["day"] = 2
    assert ui.header()["chips"] == [{"text": "Day 2", "class": ""}]


def test_normalizeHeader_accepts_strings_dicts_and_nothing():
    assert normalizeHeader(None) == {"title": "", "chips": []}
    assert normalizeHeader({}) == {"title": "", "chips": []}
    assert normalizeHeader(
        {"title": "T", "chips": ["a", {"text": "b", "class": "low"}, {"text": 3}]}
    ) == {
        "title": "T",
        "chips": [
            {"text": "a", "class": ""},
            {"text": "b", "class": "low"},
            {"text": "3", "class": ""},
        ],
    }


def test_showBusy_is_inherited_and_only_waits(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    ScriptedUI([]).showBusy("Working...", seconds=0.25)
    assert slept == [0.25]


def test_promptForNumber_parses_or_returns_none():
    assert ScriptedUI(["12.5"]).promptForNumber("How many?") == 12.5
    assert ScriptedUI(["twelve"]).promptForNumber("How many?") is None


def test_inherited_interactive_dialogue_uses_primitives():
    npc = NPC(
        "Sam", "Sails.", [{"question": "Boats?", "response": "Rowboat's for sale."}]
    )
    ui = ScriptedUI(["1", "2"])  # ask the question, then [Back]
    ui.currentPrompt.text = "Talking"
    ui.showInteractiveDialogue(npc)
    assert ui.shown == [
        ("options", "Talking with Sam", ["Boats?", "[Back]"]),
        ("dialogue", "Sam: Rowboat's for sale."),
        ("options", "Talking with Sam", ["Boats?", "[Back]"]),
    ]
    assert ui.currentPrompt.text == Prompt.DEFAULT


def test_inherited_interactive_dialogue_falls_back_to_introduction_when_no_options():
    ui = ScriptedUI([])
    ui.showInteractiveDialogue(NPC("Tom", "Old."))
    assert ui.shown == [("dialogue", "Tom: Old.")]


def test_inherited_interactive_dialogue_reflects_unlocked_options():
    known = []
    npc = NPC(
        "Margaret",
        "",
        [
            {"question": "Hello", "response": "Hello."},
            {
                "question": "Secret?",
                "response": "Yes.",
                "condition": lambda: bool(known),
            },
        ],
    )
    ui = ScriptedUI(["2"])
    ui.showInteractiveDialogue(npc)
    assert ui.shown[0][2] == ["Hello", "[Back]"]
    known.append("x")
    ui = ScriptedUI(["3"])
    ui.showInteractiveDialogue(npc)
    assert ui.shown[0][2] == ["Hello", "Secret?", "[Back]"]


def test_unavailableReasons_maps_option_numbers_onto_a_parallel_list():
    ui = ScriptedUI([])
    assert ui.unavailableReasons(["a", "b", "c"], {2: "tired"}) == [None, "tired", None]


def test_unavailableReasons_defaults_to_everything_available():
    ui = ScriptedUI([])
    assert ui.unavailableReasons(["a", "b"], None) == [None, None]
    assert ui.unavailableReasons([], {}) == []


def test_unavailableReasons_rejects_a_number_outside_the_menu():
    ui = ScriptedUI([])
    with pytest.raises(ValueError) as error:
        ui.unavailableReasons(["a", "b"], {3: "nope"})
    assert "only has 2 option(s)" in str(error.value)


def test_unavailableReasons_never_blocks_every_option():
    ui = ScriptedUI([])
    assert ui.unavailableReasons(["a", "b"], {1: "x", 2: "y"}) == [None, None]


def test_selectableNumbers_excludes_the_unavailable_rows():
    ui = ScriptedUI([])
    assert ui.selectableNumbers([None, "tired", None]) == {"1", "3"}


def test_unavailable_wording_is_shared_by_the_text_front_ends():
    assert unavailableSuffix(None) == ""
    assert unavailableSuffix("needs 10 energy") == " (unavailable: needs 10 energy)"
    assert (
        unavailableMessage("needs 10 energy")
        == "You can't do that right now: needs 10 energy."
    )
