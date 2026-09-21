import builtins
import time

from tak import NPC, Prompt
from tak.ui.console import ConsoleUserInterface
from tak.ui.factory import createUserInterface
from tak.ui.uitype import UIType
from conftest import makeHeader


def makeConsole(chips=None):
    return ConsoleUserInterface(Prompt(), makeHeader(chips))


def feed(monkeypatch, answers):
    answers = list(answers)
    monkeypatch.setattr(builtins, "input", lambda *args: answers.pop(0))


def test_factory_builds_the_console_front_end():
    ui = createUserInterface(UIType.CONSOLE, Prompt())
    assert isinstance(ui, ConsoleUserInterface)


def test_showOptions_prints_header_prompt_and_numbered_options(monkeypatch, capsys):
    ui = makeConsole(["Day 3", {"text": "8:00 AM"}, "Loop 2"])
    ui.currentPrompt.text = "The tide is out."
    feed(monkeypatch, ["2"])
    assert ui.showOptions("The Docks", ["Fish", "Leave"]) == "2"
    out = capsys.readouterr().out
    assert " The Docks" in out
    assert " Day 3" in out and " | 8:00 AM" in out and " | Loop 2" in out
    assert "The tide is out." in out
    assert " [1] Fish" in out and " [2] Leave" in out


def test_showOptions_with_no_header_prints_no_chips(monkeypatch, capsys):
    ui = ConsoleUserInterface(Prompt())
    feed(monkeypatch, ["1"])
    ui.showOptions("Home", ["Sleep"])
    assert " | " not in capsys.readouterr().out


def test_showOptions_retries_until_a_listed_number_is_entered(monkeypatch):
    ui = makeConsole()
    feed(monkeypatch, ["x", "9", "1"])
    assert ui.showOptions("Home", ["Sleep"]) == "1"
    # "Try again!" was written to the prompt for the bad entries
    assert ui.currentPrompt.text == "Try again!"


def test_showOptions_tags_an_unavailable_row_and_refuses_it(monkeypatch, capsys):
    ui = makeConsole()
    feed(monkeypatch, ["1", "2"])
    assert ui.showOptions("Docks", ["Fish", "Leave"], {1: "too tired"}) == "2"
    out = capsys.readouterr().out
    assert " [1] Fish (unavailable: too tired)" in out
    assert ui.currentPrompt.text == "You can't do that right now: too tired."


def test_showDialogue_waits_then_resets_the_prompt(monkeypatch, capsys):
    ui = makeConsole()
    ui.currentPrompt.text = "Something else"
    feed(monkeypatch, [""])
    ui.showDialogue("A gull lands on the rail.")
    assert "A gull lands on the rail." in capsys.readouterr().out
    assert ui.currentPrompt.text == Prompt.DEFAULT


def test_showInteractiveDialogue_asks_then_backs_out(monkeypatch, capsys):
    npc = NPC("Sam", "Sails.", [{"question": "Boats?", "response": "For sale."}])
    ui = makeConsole()
    feed(monkeypatch, ["1", "", "2"])
    ui.showInteractiveDialogue(npc)
    out = capsys.readouterr().out
    assert " [1] Boats?" in out and " [2] [Back]" in out
    assert " Sam: For sale." in out


def test_showInteractiveDialogue_with_no_options_introduces(monkeypatch, capsys):
    ui = makeConsole()
    feed(monkeypatch, [""])
    ui.showInteractiveDialogue(NPC("Tom", "Old."))
    assert "Tom: Old." in capsys.readouterr().out


def test_showInteractiveDialogue_rejects_a_bad_choice(monkeypatch, capsys):
    npc = NPC("Sam", "", [{"question": "?", "response": "!"}])
    ui = makeConsole()
    feed(monkeypatch, ["7", "", "2"])
    ui.showInteractiveDialogue(npc)
    assert "Invalid choice" in capsys.readouterr().out


def test_promptForText_and_number(monkeypatch):
    ui = makeConsole()
    feed(monkeypatch, ["Harbourmaster", "4", "no"])
    assert ui.promptForText("Name?") == "Harbourmaster"
    assert ui.promptForNumber("How many?") == 4.0
    assert ui.promptForNumber("How many?") is None


def test_showBusy_prints_the_message_and_waits_in_whole_seconds(monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    makeConsole().showBusy("Fishing...", seconds=2.5)
    assert slept == [1.0, 1.0, 0.5]
    assert "Fishing..." in capsys.readouterr().out


def test_timedKeyPress_returns_reaction_seconds(monkeypatch):
    ticks = iter([100.0, 100.4])
    monkeypatch.setattr(time, "time", lambda: next(ticks))
    feed(monkeypatch, [""])
    assert abs(makeConsole().timedKeyPress("Now!") - 0.4) < 1e-9


def test_timedKeyPress_counts_an_interrupt_as_a_miss(monkeypatch):
    def interrupted(*args):
        raise EOFError

    monkeypatch.setattr(builtins, "input", interrupted)
    assert makeConsole().timedKeyPress("Now!") == float("inf")


def test_cleanup_is_a_no_op():
    makeConsole().cleanup()
