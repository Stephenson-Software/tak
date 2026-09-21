# @author Daniel McCoy Stephenson
"""The user-interface contract and the front-ends that implement it.

A game talks to the player only through BaseUserInterface's primitives -
showOptions, showDialogue, promptForText, timedKeyPress, showBusy - so the same
synchronous game loop plays in a terminal, in a browser that polls a server, or
entirely inside a browser tab under Pyodide. Pick one with UIType and
createUserInterface; the game never imports a concrete front-end.
"""

from tak.ui.base import BaseUserInterface, unavailableMessage, unavailableSuffix
from tak.ui.uitype import UIType
from tak.ui.factory import createUserInterface

__all__ = [
    "BaseUserInterface",
    "UIType",
    "createUserInterface",
    "unavailableMessage",
    "unavailableSuffix",
]
