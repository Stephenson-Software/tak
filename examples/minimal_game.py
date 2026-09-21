#!/usr/bin/env python3
# @author Daniel McCoy Stephenson
"""The smallest complete tak game: three scenes, one villager, no saves.

Run it in a terminal, or in a browser that polls this process:

    python3 examples/minimal_game.py          # console
    python3 examples/minimal_game.py --web    # then open http://127.0.0.1:8000

Everything a game needs from the kit is on show here: a Prompt (the line
above every menu), a header provider (the status chips), one front-end made
by createUserInterface, an NPC with a line that unlocks when you know
something, and a loop of scenes that each show a menu and say where to go
next. Copy the file and replace the words.
"""

import sys

from tak import NPC, Prompt
from tak.ui import UIType, createUserInterface


def main(uiType=None, makeUI=None):
    # --- state -----------------------------------------------------------
    # Whatever your game remembers. Here: where you are and what you know.
    state = {"scene": "gate", "known": set(), "hour": 8}
    prompt = Prompt("You are at the gate. What would you like to do?")

    # The header provider is read fresh before every menu; the kit renders
    # the chips and knows nothing about what they mean.
    def header():
        return {
            "title": "Gatehouse",
            "chips": ["%d:00" % state["hour"], state["scene"]],
        }

    # Tests pass a makeUI that builds a scripted front-end from the same two
    # arguments; that is the only seam a game needs for testing.
    if makeUI is None:
        ui = createUserInterface(
            uiType or UIType.CONSOLE, prompt, header, title="Gatehouse"
        )
    else:
        ui = makeUI(prompt, header)

    # --- a villager --------------------------------------------------------
    # A "condition" hides a line until it is true; a callable "response" is
    # evaluated when spoken, so it can change the state.
    def tellSecret():
        state["known"].add("password")
        return "The word is 'oyster'. Don't say I told you."

    keeper = NPC(
        "the gatekeeper",
        "The gatekeeper leans on the gate and looks at nothing in particular.",
        [
            {"question": "Nice weather.", "response": "Is it."},
            {
                "question": "The innkeeper sent me.",
                "response": tellSecret,
                "condition": lambda: "inn" in state["known"],
            },
        ],
    )

    # --- scenes ------------------------------------------------------------
    # Each scene shows one menu, acts on the choice, and returns the next
    # scene id. Menus are built as parallel option/action lists so rows can
    # come and go without the numbers drifting.
    def gate():
        options, actions, unavailable = [], [], {}
        options.append("Talk to the gatekeeper")
        actions.append("talk")
        options.append("Go to the inn")
        actions.append("inn")
        options.append("Say the password")
        actions.append("password")
        if "password" not in state["known"]:
            # Listed but unpickable, with the reason - the kit greys it out.
            unavailable[len(options)] = "you don't know it yet"
        options.append("Quit")
        actions.append("quit")
        choice = actions[int(ui.showOptions("The gate", options, unavailable)) - 1]
        if choice == "talk":
            ui.showInteractiveDialogue(keeper)
            return "gate"
        if choice == "password":
            return "beyond"
        return choice

    def inn():
        choice = int(
            ui.showOptions("The inn", ["Ask about the gate", "Back to the gate"])
        )
        if choice == 1:
            state["known"].add("inn")
            ui.showDialogue("The innkeeper says: tell the gatekeeper I sent you.")
        state["hour"] += 1
        return "inn" if choice == 1 else "gate"

    def beyond():
        ui.showDialogue(
            "The gate swings open. Beyond it, the whole game you haven't written yet."
        )
        return "quit"

    scenes = {"gate": gate, "inn": inn, "beyond": beyond}

    # --- the loop ----------------------------------------------------------
    try:
        while state["scene"] != "quit":
            state["scene"] = scenes[state["scene"]]()
            prompt.reset()
    finally:
        ui.cleanup()  # the web front-end publishes its "ended" screen here
    return state


if __name__ == "__main__":
    main(UIType.WEB if "--web" in sys.argv[1:] else UIType.CONSOLE)
