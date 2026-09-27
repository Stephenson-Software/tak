import os
import json
import shutil
from datetime import datetime

from tak.saves.browser import syncBrowserSaves

# Slots are slot_1 .. slot_99. Fixed rather than configurable: it is the bound
# the menus and the "all full" message were written against.
MAX_SLOTS = 99


# @author Daniel McCoy Stephenson
class SaveFileManager:
    """Numbered save slots under one data directory.

    A slot is a directory ``slot_N`` holding whatever files the game writes;
    ``primaryFile`` names the one that holds the run (the file whose presence
    means "there is a save here"). ``readMetadata(slotPath, primaryData)``,
    if given, returns the game-specific fields the save menu shows for a slot
    - a day number, a name, a score - merged over the ones this class always
    provides. It is only called once the primary file has parsed, and may
    raise nothing: a slot whose other files are damaged is still a slot.
    """

    def __init__(
        self, data_directory="data", primaryFile="save.json", readMetadata=None
    ):
        self.data_directory = data_directory
        self.primaryFile = primaryFile
        self._readMetadata = readMetadata
        self.selected_save_slot = None

    def list_save_files(self):
        """Returns a list of available save file slots with their metadata"""
        if not os.path.exists(self.data_directory):
            return []

        save_files = []
        try:
            for entry in os.listdir(self.data_directory):
                if not entry.startswith("slot_"):
                    continue

                _, _, suffix = entry.partition("_")
                if not suffix.isdigit():
                    continue

                slot_index = int(suffix)
                if slot_index < 1 or slot_index > MAX_SLOTS:
                    continue

                slot_path = os.path.join(self.data_directory, entry)
                if not os.path.isdir(slot_path):
                    continue

                metadata = self._read_save_metadata(slot_path)
                if metadata:
                    save_files.append(
                        {
                            "slot": slot_index,
                            "slot_name": entry,
                            "path": slot_path,
                            "metadata": metadata,
                        }
                    )
        except OSError:
            return []

        save_files.sort(key=lambda save: save["slot"])
        return save_files

    def _read_save_metadata(self, slot_path):
        """Read metadata from a save slot.

        Returns None only when the slot holds no run at all (no primary file).
        A primary file that will not parse comes back as the marker described
        in _unreadable_save_metadata rather than as None, so a damaged slot
        stays listed and stays claimed instead of disappearing."""
        primary_path = os.path.join(slot_path, self.primaryFile)

        if not os.path.exists(primary_path):
            return None

        # Deliberately no "size > 0" guard: an empty file is a damaged file
        # rather than an absent one, and skipping the read for it is what let a
        # zero-byte save be offered in the menu as a real one.
        try:
            with open(primary_path, "r") as f:
                primary_data = json.load(f)
        except (json.JSONDecodeError, IOError, OSError) as error:
            return self._unreadable_save_metadata(primary_path, error)

        if not isinstance(primary_data, dict):
            # Valid JSON that is not an object - a bare number, or some other
            # file copied over the save - has no fields to read.
            return self._unreadable_save_metadata(
                primary_path, ValueError("not a JSON object")
            )

        metadata = {}
        if self._readMetadata is not None:
            metadata.update(self._readMetadata(slot_path, primary_data) or {})
        metadata["last_modified"] = self._last_modified(primary_path)
        return metadata

    def _last_modified(self, path):
        """A file's modification time as a display string, or None if unknown."""
        try:
            return datetime.fromtimestamp(os.path.getmtime(path)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except OSError:
            return None

    def _unreadable_save_metadata(self, primary_path, error):
        """Metadata standing in for a slot whose primary file will not parse.

        Returned instead of None because list_save_files() drops a slot with no
        metadata, and get_next_available_slot() derives the taken slot numbers
        from that same filtered list - so a damaged slot used to vanish from the
        menu *and* be handed straight back as "Create New Save", pointing the
        next save at the occupied directory and overwriting the intact files
        sitting beside the damaged one.

        Callers key off "unreadable" to show the slot as present but unpickable."""
        return {
            "unreadable": True,
            "reason": str(error),
            "last_modified": self._last_modified(primary_path),
        }

    def get_next_available_slot(self):
        """Returns the next available save slot number, or None if all slots are full"""
        save_files = self.list_save_files()
        if not save_files:
            return 1

        existing_slots = sorted([save["slot"] for save in save_files])
        for i in range(1, MAX_SLOTS + 1):
            if i not in existing_slots:
                return i
        return None

    def select_save_slot(self, slot_number):
        """Select a save slot to use"""
        self.selected_save_slot = slot_number

    def get_save_path(self, filename):
        """Get the full path for a save file in the selected slot"""
        if self.selected_save_slot is None:
            # Name the call that was skipped: a game reaching for a path
            # straight after building the manager has not chosen a slot yet.
            raise ValueError(
                "SaveFileManager.get_save_path(%r) was called before a slot was "
                "selected. Call select_save_slot(n) first - or open the game on "
                "tak.saves.chooseSlot(), which selects the slot the player "
                "picked." % (filename,)
            )

        slot_name = f"slot_{self.selected_save_slot}"
        slot_path = os.path.join(self.data_directory, slot_name)

        if not os.path.exists(slot_path):
            os.makedirs(slot_path, exist_ok=True)

        return os.path.join(slot_path, filename)

    def delete_save_slot(self, slot_number):
        """Delete a save slot"""
        slot_name = f"slot_{slot_number}"
        slot_path = os.path.join(self.data_directory, slot_name)

        if os.path.exists(slot_path):
            shutil.rmtree(slot_path)
            # Browser storage mirrors the save directory wholesale, so a
            # deletion has to be flushed too - otherwise the slot comes back
            # on the next page load. A no-op outside the Pyodide front-end.
            syncBrowserSaves()
            return True
        return False
