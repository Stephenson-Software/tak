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
    assert ui.menus[1][0] == "Delete a Save File"
    assert ui.menus[1][1] == ["Delete Slot 1", "Cancel"]
    assert ui.menus[2][1] == ["Yes, delete it", "No, keep it"]


def test_delete_can_be_cancelled_twice_over(tmp_path):
    writeSlot(tmp_path, 1)
    # Delete -> Cancel; Delete -> slot 1 -> No; then Quit
    ui = ScriptedUI(["3", "2", "3", "1", "2", "4"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) is None
    assert os.path.exists(tmp_path / "slot_1")
    assert ui.dialogues == []


def test_a_damaged_choice_a_front_end_let_through_is_explained(tmp_path):
    writeSlot(tmp_path, 1, content="{oops")
    # A conforming front-end never returns "1" here; a broken one might.
    ui = ScriptedUI(["1", "2"])
    assert chooseSlot(ui, manager(str(tmp_path)), "Tidewater", describe) == ("new", 2)
    assert "could not be read" in ui.dialogues[0]
    assert "save.json" in ui.dialogues[0]
    assert "Delete a Save File" in ui.dialogues[0]


def test_a_failed_delete_is_reported(tmp_path, monkeypatch):
    writeSlot(tmp_path, 1)
    m = manager(str(tmp_path))
    monkeypatch.setattr(m, "delete_save_slot", lambda slot: False)
    ui = ScriptedUI(["3", "1", "1", "4"])  # the slot is still there, so Quit is 4
    assert chooseSlot(ui, m, "Tidewater", describe) is None
    assert ui.dialogues == ["Failed to delete Slot 1."]


# -- A new run is never written over files a slot already holds --------------
# The browser runtime once showed an empty slot list while the slots were on
# disk; "Create New Save (Slot 1)" would then have written over slot 1.
# Whatever makes the list come up short, "free" is decided from the disk.

import tak.saves.manager as managerModule  # noqa: E402


def readAll(root):
    found = {}
    for directory, _, files in os.walk(root):
        for name in files:
            path = os.path.join(directory, name)
            with open(path) as f:
                found[os.path.relpath(path, root)] = f.read()
    return found


def test_a_slot_holding_only_other_files_is_not_handed_out(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "slot_1"))
    with open(os.path.join(root, "slot_1", "stats.json"), "w") as f:
        f.write('{"kept": true}')
    m = manager(root)
    assert m.list_save_files() == []  # no primary file: not a loadable save
    assert m.get_next_available_slot() == 2
    ui = ScriptedUI(["1"])
    assert chooseSlot(ui, m, "T", describe) == ("new", 2)
    assert ui.menus[0][1][0] == "Create New Save (Slot 2)"


def test_an_empty_slot_directory_is_free(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "slot_1", "nested"))
    assert manager(root).get_next_available_slot() == 1


def test_one_unreadable_slot_does_not_hide_the_others(tmp_path):
    root = str(tmp_path)
    writeSlot(root, 1)
    writeSlot(root, 2)

    def readMetadata(path, data):
        if path.endswith("slot_1"):
            raise OSError("disk hiccup")
        return {"loop": data["loop"]}

    m = SaveFileManager(root, readMetadata=readMetadata)
    saves = m.list_save_files()
    assert [s["slot"] for s in saves] == [1, 2]
    assert saves[0]["metadata"]["unreadable"] is True
    assert m.get_next_available_slot() == 3


def test_an_unreadable_save_directory_offers_no_new_slot(tmp_path, monkeypatch):
    root = str(tmp_path)
    writeSlot(root, 1)

    def refuse(path):
        raise OSError("cannot list")

    monkeypatch.setattr(managerModule.os, "listdir", refuse)
    m = manager(root)
    assert m.list_save_files() == []
    assert m.get_next_available_slot() is None


def test_slots_hidden_from_the_list_are_still_taken(tmp_path, monkeypatch):
    # The reported symptom: the list comes back empty although the slots are
    # on disk. "Create New Save" must not point at slot 1.
    root = str(tmp_path)
    writeSlot(root, 1, '{"loop": 7}')
    writeSlot(root, 2, '{"loop": 9}')
    before = readAll(root)
    m = manager(root)
    monkeypatch.setattr(m, "list_save_files", lambda: [])
    assert m.get_next_available_slot() == 3
    ui = ScriptedUI(["1"])
    assert chooseSlot(ui, m, "T", describe) == ("new", 3)
    assert readAll(root) == before


def test_choose_slot_never_offers_a_new_run_in_an_occupied_slot(tmp_path):
    root = str(tmp_path)
    writeSlot(root, 1, '{"loop": 7}')
    before = readAll(root)

    class Stale(SaveFileManager):
        def list_save_files(self):
            return []

        def get_next_available_slot(self):
            return 1  # what an empty list used to produce

    m = Stale(root, readMetadata=lambda p, d: {"loop": d["loop"]})
    ui = ScriptedUI(["1"])  # the only option left: Quit
    assert chooseSlot(ui, m, "T", describe) is None
    assert ui.menus[0][1] == ["Quit"]
    assert m.selected_save_slot is None
    assert readAll(root) == before


def test_choose_slot_with_a_stand_in_manager_still_offers_a_new_save():
    # Games test their menus with MagicMock managers, whose slot_holds_files
    # answers with a mock: that must not read as "occupied" (it once made
    # FishE's test loop forever).
    from unittest.mock import MagicMock

    m = MagicMock()
    m.list_save_files.return_value = []
    m.get_next_available_slot.return_value = 1
    ui = ScriptedUI(["1"])
    assert chooseSlot(ui, m, "T", describe) == ("new", 1)
    m.select_save_slot.assert_called_once_with(1)
