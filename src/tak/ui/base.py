import time
from abc import ABC, abstractmethod

from tak.prompt import Prompt


def unavailableSuffix(reason):
    """How a text front-end tags a menu row the game would refuse.

    The web front-ends send the bare reason to the browser instead, which
    styles it rather than appending it to the label."""
    return "" if reason is None else " (unavailable: %s)" % reason


def unavailableMessage(reason):
    """What to say when the player picks an option that can't be picked.

    Names the blocker and what to do about it rather than a bare "try again",
    which would read as though they had mistyped."""
    return "You can't do that right now: %s." % reason


def _emptyHeader():
    return {"title": "", "chips": []}


def normalizeHeader(header):
    """The header a game's provider returned, in the one shape front-ends read.

    A provider may return chips as bare strings or as {"text", "class"} dicts;
    the console prints text and ignores class, the browser styles by it. A
    provider that returns None (or nothing useful) gets an empty header, so a
    game with no status line to show never has to write a provider at all."""
    if not header:
        return _emptyHeader()
    chips = []
    for chip in header.get("chips") or []:
        if isinstance(chip, dict):
            chips.append(
                {"text": str(chip.get("text", "")), "class": chip.get("class") or ""}
            )
        else:
            chips.append({"text": str(chip), "class": ""})
    return {"title": str(header.get("title") or ""), "chips": chips}


# @author Daniel McCoy Stephenson
class BaseUserInterface(ABC):
    """Abstract contract every front-end (console, web, pyodide) implements.

    Concrete subclasses implement the rendering/input primitives below. Shared
    state and the higher-level interactive-dialogue flow live here so all
    front-ends behave consistently and only the primitives differ.

    ``header`` is a zero-argument callable the game supplies; it returns the
    status line to draw above every menu, as {"title": str, "chips": [...]}.
    The kit knows nothing about days, money or energy - a game's header is
    whatever its provider says it is, read fresh each time a menu is drawn.
    """

    def __init__(self, currentPrompt: Prompt, header=None):
        self.currentPrompt = currentPrompt
        self._headerProvider = header
        self.optionList = []

    def header(self):
        """The current header, normalized - see normalizeHeader."""
        if self._headerProvider is None:
            return _emptyHeader()
        return normalizeHeader(self._headerProvider())

    @abstractmethod
    def lotsOfSpace(self):
        """Clear or add space to the display."""

    @abstractmethod
    def divider(self):
        """Display a divider between sections."""

    @abstractmethod
    def showOptions(self, descriptor, optionList, unavailableOptions=None):
        """Show numbered options and return the chosen option's number as a string.

        unavailableOptions is an optional {optionNumber: reason} mapping naming
        the 1-based options the game would refuse right now, each with a short
        reason ("needs 10 energy - sleep at home"). Every front-end must show
        those options as unpickable, spell the reason out beside them, and
        refuse to return their number - see unavailableReasons()."""

    def unavailableReasons(self, optionList, unavailableOptions):
        """One entry per option: the reason it can't be picked, or None.

        Call sites pass {optionNumber: reason} rather than a parallel list
        because a menu is built by appending, so the row just added is always
        len(optionList) and the numbers can't drift out of step as options
        appear and disappear with the player's progress. Front-ends want the
        parallel list, so the conversion happens once, here.
        """
        reasons = [None] * len(optionList)
        for number, reason in (unavailableOptions or {}).items():
            if not 1 <= number <= len(optionList):
                raise ValueError(
                    "showOptions was told option %r is unavailable (%r), but "
                    "the menu only has %d option(s). The keys of "
                    "unavailableOptions are 1-based option numbers into the "
                    "list passed alongside it - a menu that appends its rows "
                    "should mark one with len(optionList) right after "
                    "appending it." % (number, reason, len(optionList))
                )
            reasons[number - 1] = reason
        # Marking every option unavailable would leave the player facing a menu
        # that accepts nothing, which no front-end can recover from. Fall back
        # to a normal menu instead and let the game give its own refusal.
        if optionList and all(reason is not None for reason in reasons):
            return [None] * len(optionList)
        return reasons

    def selectableNumbers(self, reasons):
        """The 1-based option numbers a front-end may return, as strings."""
        return {
            str(index + 1) for index, reason in enumerate(reasons) if reason is None
        }

    @abstractmethod
    def showDialogue(self, text):
        """Show a block of text and wait for the player to acknowledge it."""

    @abstractmethod
    def promptForText(self, promptText):
        """Show a prompt and return the line of text the player enters."""

    @abstractmethod
    def timedKeyPress(self, message):
        """Show a message and return the seconds until the player reacts.

        Used by timing challenges; a front-end that cannot measure a reaction
        may return 0.0 to count it as instant."""

    @abstractmethod
    def cleanup(self):
        """Release any resources held by the front-end."""

    def showBusy(self, message, seconds=1.0):
        """Show a message while the game pauses, without waiting for input.

        For short beats that otherwise have no way to say anything:
        showDialogue would demand a keypress the game never asked for.
        Concrete (not abstract) so a front-end that has nothing to draw still
        gets the pause; every front-end in the kit overrides it to actually
        show the message."""
        time.sleep(seconds)

    def promptForNumber(self, promptText):
        """Prompt for a number via promptForText; return a float or None if the
        player's input was not numeric. Works for every front-end."""
        try:
            return float(self.promptForText(promptText))
        except (ValueError, TypeError):
            return None

    def showInteractiveDialogue(self, npc):
        """Default interactive NPC conversation built on the primitives above.

        Front-ends inherit this for free; the console overrides it with a
        richer layout. Picks a question via showOptions and shows the response
        via showDialogue until the player chooses to go back."""
        while True:
            dialogueOptions = npc.get_dialogue_options()
            if not dialogueOptions:
                self.showDialogue(npc.introduce())
                self.currentPrompt.reset()
                return

            questions = [
                option.get("question", "Option %d" % (index + 1))
                for index, option in enumerate(dialogueOptions)
            ]
            questions.append("[Back]")

            choice = int(self.showOptions("Talking with %s" % npc.name, questions))
            if choice == len(questions):
                self.currentPrompt.reset()
                return

            self.showDialogue(
                "%s: %s" % (npc.name, npc.get_dialogue_response(choice - 1))
            )
