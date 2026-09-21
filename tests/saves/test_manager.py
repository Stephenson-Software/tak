import json
import os

import pytest

from tak.saves import SaveFileManager, MAX_SLOTS
from tak.saves import manager as managerModule


def writeSlot(root, n, primary="save.json", content=None, **others):
    slot = os.path.join(root, "slot_%d" % n)
    os.makedirs(slot, exist_ok=True)
    with open(os.path.join(slot, primary), "w") as f:
        f.write(
            json.dumps({"loop": n} if content is None else content)
            if not isinstance(content, str)
            else content
        )
    for name, data in others.items():
        with open(os.path.join(slot, name), "w") as f:
            f.write(data)
    return slot


def test_defaults():
    m = SaveFileManager()
    assert m.data_directory == "data" and m.primaryFile == "save.json"
    assert m.selected_save_slot is None


def test_list_save_files_empty_when_directory_is_missing(tmp_path):
    assert SaveFileManager(str(tmp_path / "nope")).list_save_files() == []


def test_list_save_files_reads_slots_sorted_with_game_metadata(tmp_path):
    writeSlot(tmp_path, 3)
    writeSlot(tmp_path, 1)
    m = SaveFileManager(
        str(tmp_path), readMetadata=lambda path, data: {"loop": data.get("loop")}
    )
    saves = m.list_save_files()
    assert [s["slot"] for s in saves] == [1, 3]
    assert saves[0]["slot_name"] == "slot_1"
    assert saves[0]["metadata"]["loop"] == 1
    assert saves[0]["metadata"]["last_modified"]


def test_list_save_files_without_a_metadata_reader_still_lists(tmp_path):
    writeSlot(tmp_path, 2)
    saves = SaveFileManager(str(tmp_path)).list_save_files()
    assert len(saves) == 1 and "last_modified" in saves[0]["metadata"]


def test_list_save_files_honours_the_primary_file_name(tmp_path):
    writeSlot(tmp_path, 1, primary="player.json")
    assert SaveFileManager(str(tmp_path)).list_save_files() == []
    assert (
        len(SaveFileManager(str(tmp_path), primaryFile="player.json").list_save_files())
        == 1
    )


def test_list_save_files_ignores_invalid_names_and_files(tmp_path):
    for name in (
        "slot_",
        "slot_abc",
        "slot_0",
        "slot_%d" % (MAX_SLOTS + 1),
        "other",
        "slot_-1",
    ):
        os.makedirs(tmp_path / name, exist_ok=True)
    (tmp_path / "slot_5").write_text("a file, not a directory")
    os.makedirs(tmp_path / "slot_7")  # no primary file: not a save
    assert SaveFileManager(str(tmp_path)).list_save_files() == []


def test_list_save_files_oserror_handling(tmp_path, monkeypatch):
    writeSlot(tmp_path, 1)

    def boom(path):
        raise OSError("no")

    monkeypatch.setattr(os, "listdir", boom)
    assert SaveFileManager(str(tmp_path)).list_save_files() == []


def test_get_next_available_slot(tmp_path):
    m = SaveFileManager(str(tmp_path))
    assert m.get_next_available_slot() == 1
    writeSlot(tmp_path, 1)
    writeSlot(tmp_path, 2)
    writeSlot(tmp_path, 4)
    assert m.get_next_available_slot() == 3


def test_get_next_available_slot_all_full(tmp_path):
    for n in range(1, MAX_SLOTS + 1):
        writeSlot(tmp_path, n)
    assert SaveFileManager(str(tmp_path)).get_next_available_slot() is None


def test_get_save_path_needs_a_slot_and_creates_it(tmp_path):
    m = SaveFileManager(str(tmp_path))
    with pytest.raises(ValueError):
        m.get_save_path("save.json")
    m.select_save_slot(4)
    path = m.get_save_path("save.json")
    assert path == os.path.join(str(tmp_path), "slot_4", "save.json")
    assert os.path.isdir(os.path.join(str(tmp_path), "slot_4"))


def test_delete_save_slot_removes_the_directory_and_flushes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(managerModule, "syncBrowserSaves", lambda: calls.append(1))
    writeSlot(tmp_path, 1, extra="x")
    m = SaveFileManager(str(tmp_path))
    assert m.delete_save_slot(1) is True
    assert not os.path.exists(tmp_path / "slot_1")
    assert calls == [1]


def test_delete_of_a_missing_slot_does_not_flush(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(managerModule, "syncBrowserSaves", lambda: calls.append(1))
    assert SaveFileManager(str(tmp_path)).delete_save_slot(9) is False
    assert calls == []


@pytest.mark.parametrize("content", ["", "{not json", "42", "[1, 2]"])
def test_a_damaged_primary_file_keeps_the_slot_listed_and_claimed(tmp_path, content):
    writeSlot(tmp_path, 1, content=content)
    m = SaveFileManager(str(tmp_path), readMetadata=lambda p, d: {"loop": d["loop"]})
    saves = m.list_save_files()
    assert len(saves) == 1
    assert saves[0]["metadata"]["unreadable"] is True
    assert saves[0]["metadata"]["reason"]
    # The next new save must not be pointed at the occupied directory.
    assert m.get_next_available_slot() == 2


def test_metadata_reader_errors_are_the_games_problem_but_last_modified_survives_a_bad_mtime(
    tmp_path, monkeypatch
):
    writeSlot(tmp_path, 1)
    m = SaveFileManager(str(tmp_path))

    def badMtime(path):
        raise OSError("no")

    monkeypatch.setattr(os.path, "getmtime", badMtime)
    assert m.list_save_files()[0]["metadata"]["last_modified"] is None


def test_metadata_reader_may_return_none(tmp_path):
    writeSlot(tmp_path, 1)
    m = SaveFileManager(str(tmp_path), readMetadata=lambda p, d: None)
    assert "last_modified" in m.list_save_files()[0]["metadata"]


def test_full_workflow(tmp_path):
    m = SaveFileManager(str(tmp_path))
    slot = m.get_next_available_slot()
    m.select_save_slot(slot)
    with open(m.get_save_path("save.json"), "w") as f:
        json.dump({"loop": 1}, f)
    assert [s["slot"] for s in m.list_save_files()] == [1]
    assert m.get_next_available_slot() == 2
    assert m.delete_save_slot(1)
    assert m.list_save_files() == []
