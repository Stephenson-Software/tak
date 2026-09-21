# @author Daniel McCoy Stephenson
"""The save-slot menu every kit game opens on.

Lists the slots, offers a new one, lets the player delete one, and returns
what they chose. A damaged slot is shown rather than hidden, and unpickable
rather than loadable: hiding it is what let a slot be handed back as "Create
New Save" and overwritten (see SaveFileManager._unreadable_save_metadata);
offering it as a save would promise a run that cannot be read. Deleting it is
how the slot gets reclaimed, so the reason says so.
"""

DAMAGED_REASON = "can't be read - delete it to reuse the slot"


def chooseSlot(userInterface, saveFileManager, title, describe):
    """Run the menu until the player picks a slot or quits.

    describe(metadata) returns the text shown after "Load Slot N" for a
    readable slot - the game's summary of the run ("Loop 3, 6 facts").

    Returns ("load", slot) for an existing save, ("new", slot) for a fresh
    one, or None if the player quit. On either slot result the manager's
    selected slot is already set."""
    while True:  # loop instead of recursion so a long session can't overflow
        save_files = saveFileManager.list_save_files()
        options = []
        actions = []
        unavailable = {}
        for save in save_files:
            metadata = save["metadata"]
            if metadata.get("unreadable"):
                options.append("Slot %d (damaged)" % save["slot"])
                actions.append(("damaged", save["slot"]))
                unavailable[len(options)] = DAMAGED_REASON
                continue
            summary = describe(metadata)
            options.append(
                "Load Slot %d%s" % (save["slot"], " (%s)" % summary if summary else "")
            )
            actions.append(("load", save["slot"]))

        next_slot = saveFileManager.get_next_available_slot()
        if next_slot is not None:
            options.append("Create New Save (Slot %d)" % next_slot)
            actions.append(("new", next_slot))
        if save_files:
            options.append("Delete a Save File")
            actions.append(("delete", None))
        options.append("Quit")
        actions.append(("quit", None))

        choice = int(userInterface.showOptions(title, options, unavailable))
        kind, arg = actions[choice - 1]

        if kind in ("load", "new"):
            saveFileManager.select_save_slot(arg)
            return kind, arg
        elif kind == "delete":
            deleteSlot(userInterface, saveFileManager, title, save_files)
        elif kind == "quit":
            return None
        # "damaged": a conforming front-end never returns it; loop regardless.


def deleteSlot(userInterface, saveFileManager, title, save_files):
    """Offer the slots for deletion. Returns True if one was deleted."""
    options = []
    for save in save_files:
        damaged = " (damaged)" if save["metadata"].get("unreadable") else ""
        options.append("Delete Slot %d%s" % (save["slot"], damaged))
    options.append("Cancel")

    choice = int(userInterface.showOptions(title + " - Delete", options))
    if choice == len(options):
        return False
    slot = save_files[choice - 1]["slot"]

    confirm = int(
        userInterface.showOptions(
            "Delete Slot %d? This cannot be undone." % slot, ["Yes, delete it", "No"]
        )
    )
    if confirm != 1:
        return False
    if saveFileManager.delete_save_slot(slot):
        userInterface.showDialogue("Slot %d deleted." % slot)
        return True
    userInterface.showDialogue("Slot %d could not be deleted." % slot)
    return False
