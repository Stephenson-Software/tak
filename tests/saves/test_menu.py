import json
import os

from tak import Prompt
from tak.saves import SaveFileManager, chooseSlot
from tak.ui import BaseUserInterface


class ScriptedUI(BaseUserInterface):
    def __init__(self, answers):
        super().__init__(Prompt())
        self.answers = list(answers)
        self.menus = []
        self.dialogues = []

    def lotsOfSpace(self):
        pass

    def divider(self):
        pass

    def showOptions(self, descriptor, optionList, unavailableOptions=None):
        self.menus.append(
            (descriptor, list(optionList), dict(unavailableOptions or {}))
        )
        return self.answers.pop(0)

    def showDialogue(self, text):
        self.dialogues.append(text)

    def promptForText(self, promptText):
        return self.answers.pop(0)

    def timedKeyPress(self, message):
        return 0.0

    def cleanup(self):
        pass


def writeSlot(root, n, content='{"loop": 3}'):
    os.makedirs(os.path.join(root, "slot_%d" % n), exist_ok=True)
    with open(os.path.join(root, "slot_%d" % n, "save.json"), "w") as f:
        f.write(content)


def describe(metadata):
    return "Loop %d" % metadata["loop"]


def manager(root):
    return SaveFileManager(root, readMetadata=lambda p, d: {"loop": d["loop"]})


def test_fresh_directory_offers_new_and_quit(tmp_path):
    ui = ScriptedUI(["1"])
    result = chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe)
    assert result == ("new", 1)
    assert ui.menus[0][1] == ["Create New Save (Slot 1)", "Quit"]


def test_existing_slots_are_listed_with_the_games_summary(tmp_path):
    writeSlot(tmp_path, 2)
    m = manager(str(tmp_path))
    ui = ScriptedUI(["1"])
    assert chooseSlot(ui, m, "Tidewater", describe) == ("load", 2)
    assert ui.menus[0][1] == [
        "Load Slot 2 (Loop 3)",
        "Create New Save (Slot 1)",
        "Delete a Save File",
        "Quit",
    ]
    assert m.selected_save_slot == 2


def test_a_damaged_slot_is_listed_but_unavailable(tmp_path):
    writeSlot(tmp_path, 1, content="{oops")
    ui = ScriptedUI(["2"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) == ("new", 2)
    descriptor, options, unavailable = ui.menus[0]
    assert options[0] == "Slot 1 (damaged)"
    assert unavailable == {1: "can't be read - delete it to reuse the slot"}


def test_quit_returns_none(tmp_path):
    ui = ScriptedUI(["2"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) is None


def test_delete_flow_removes_the_slot_and_returns_to_the_menu(tmp_path):
    writeSlot(tmp_path, 1)
    # menu: Delete (2) -> pick slot 1 (1) -> confirm (1) -> menu again: new (1)
    ui = ScriptedUI(["3", "1", "1", "1"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) == ("new", 1)
    assert ui.dialogues == ["Slot 1 deleted."]
    assert not os.path.exists(tmp_path / "slot_1")
    assert ui.menus[1][1] == ["Delete Slot 1", "Cancel"]


def test_delete_can_be_cancelled_twice_over(tmp_path):
    writeSlot(tmp_path, 1)
    # Delete -> Cancel; Delete -> slot 1 -> No; then Quit
    ui = ScriptedUI(["3", "2", "3", "1", "2", "4"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) is None
    assert os.path.exists(tmp_path / "slot_1")
    assert ui.dialogues == []
