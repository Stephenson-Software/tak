"""examples/minimal_game.py is what a stranger copies first; it has to run."""

import importlib.util
import os

import pytest

from tak import Prompt
from tak.ui import BaseUserInterface

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "minimal_game.py")


def loadExample():
    spec = importlib.util.spec_from_file_location("minimal_game", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptedUI(BaseUserInterface):
    """Answers menus by label (substring); records what it showed."""

    def __init__(self, script, prompt=None, header=None):
        super().__init__(prompt or Prompt(), header)
        self.script = list(script)
        self.menus = []
        self.dialogues = []
        self.cleanedUp = False

    def lotsOfSpace(self):
        pass

    def divider(self):
        pass

    def showOptions(self, descriptor, optionList, unavailableOptions=None):
        reasons = self.unavailableReasons(optionList, unavailableOptions)
        self.menus.append((descriptor, list(optionList), reasons, self.header()))
        wanted = self.script.pop(0)
        for index, label in enumerate(optionList):
            if wanted in label:
                assert reasons[index] is None, "%r is unavailable: %s" % (
                    label,
                    reasons[index],
                )
                return str(index + 1)
        raise AssertionError("%r not on menu %r" % (wanted, optionList))

    def showDialogue(self, text):
        self.dialogues.append(text)

    def promptForText(self, promptText):
        return self.script.pop(0)

    def timedKeyPress(self, message):
        return 0.0

    def cleanup(self):
        self.cleanedUp = True

    def attach(self, prompt, header):
        self.currentPrompt = prompt
        self._headerProvider = header
        return self


def test_the_example_can_be_played_to_its_end():
    module = loadExample()
    ui = ScriptedUI(
        [
            "Talk to the gatekeeper",
            "Nice weather",
            "[Back]",  # the secret is not offered yet
            "Go to the inn",
            "Ask about the gate",
            "Back to the gate",
            "Talk to the gatekeeper",
            "The innkeeper sent me",
            "[Back]",
            "Say the password",
        ]
    )
    state = module.main(makeUI=lambda prompt, header: ui.attach(prompt, header))
    assert state["scene"] == "quit"
    assert state["known"] == {"inn", "password"}
    assert ui.cleanedUp
    assert any("oyster" in text for text in ui.dialogues)
    assert any("whole game you haven't written yet" in text for text in ui.dialogues)
    # The gatekeeper's second line was hidden until the inn was visited.
    firstTalk = [m for m in ui.menus if m[0].startswith("Talking with")][0]
    assert firstTalk[1] == ["Nice weather.", "[Back]"]


def test_the_password_row_is_listed_but_unavailable_until_known():
    module = loadExample()
    ui = ScriptedUI(["Say the password"])
    with pytest.raises(AssertionError) as error:
        module.main(makeUI=lambda prompt, header: ui.attach(prompt, header))
    assert "you don't know it yet" in str(error.value)
    gate = ui.menus[0]
    assert gate[1][2] == "Say the password" and gate[2][2] == "you don't know it yet"
    assert gate[3]["chips"] == [
        {"text": "8:00", "class": ""},
        {"text": "gate", "class": ""},
    ]
