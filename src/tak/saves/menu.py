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
        elif kind == "damaged":
            # A conforming front-end refuses to return an unavailable option's
            # number, so this should be unreachable. It is handled anyway
            # because the alternative is falling out of this if-chain and
            # silently re-rendering the same menu forever, which is an
            # unexplained hang rather than a visible bug - and a new front-end
            # is exactly the thing that would get this wrong.
            userInterface.showDialogue(
                "Slot %d can't be loaded: its %s could not be read.\n\nIt has "
                "been left alone rather than overwritten, so you can still copy "
                "the folder somewhere safe. To use the slot again, choose "
                "'Delete a Save File'." % (arg, saveFileManager.primaryFile)
            )


def deleteSlot(userInterface, saveFileManager, title, save_files):
    """Offer the slots for deletion. Returns True if one was deleted.

    A damaged slot is tagged here too: this menu is the only way to reclaim
    it, so the player has to be able to tell which row is the unreadable one
    they came here to clear. ``title`` is accepted for symmetry with
    chooseSlot and reserved for a front-end that wants to show it."""
    options = []
    for save in save_files:
        damaged = " (damaged)" if save["metadata"].get("unreadable") else ""
        options.append("Delete Slot %d%s" % (save["slot"], damaged))
    options.append("Cancel")

    choice = int(userInterface.showOptions("Delete a Save File", options))
    if choice == len(options):  # Cancel
        return False
    slot = save_files[choice - 1]["slot"]

    confirm = int(
        userInterface.showOptions(
            "Permanently delete Slot %d?" % slot, ["Yes, delete it", "No, keep it"]
        )
    )
    if confirm != 1:
        return False
    if saveFileManager.delete_save_slot(slot):
        userInterface.showDialogue("Slot %d deleted." % slot)
        return True
    userInterface.showDialogue("Failed to delete Slot %d." % slot)
    return False
