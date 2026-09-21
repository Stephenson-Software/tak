# @author Daniel McCoy Stephenson
"""tak - a text-adventure kit.

One user-interface contract (tak.ui.BaseUserInterface) with three front-ends
behind it - console, server-backed web, and in-browser under Pyodide - plus
the pieces a menu-driven text game needs around it: numbered save slots with
JSON Schema validation, a progressive-disclosure unlock engine, NPC dialogue,
and the clock formatting a day-based game shows in its header.

Extracted from FishE (https://github.com/Stephenson-Software/FishE); Tidewater
is the first game built on it and FishE the second.
"""

__version__ = "0.1.0"

from tak.prompt import Prompt  # noqa: F401
from tak.npc import NPC  # noqa: F401
from tak.progression import Progression  # noqa: F401
from tak.clock import formatHour  # noqa: F401
