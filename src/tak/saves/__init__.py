# @author Daniel McCoy Stephenson
"""Numbered save slots, JSON Schema validation, and browser-storage sync."""

from tak.saves.manager import SaveFileManager, MAX_SLOTS  # noqa: F401
from tak.saves.schema import validateAgainstSchema  # noqa: F401
from tak.saves.browser import syncBrowserSaves, getJsModule  # noqa: F401
from tak.saves.menu import chooseSlot, deleteSlot  # noqa: F401
