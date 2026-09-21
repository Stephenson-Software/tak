# @author Daniel McCoy Stephenson
class Prompt:
    """The line shown above every menu ("What would you like to do?").

    A tiny mutable holder rather than a string so that a location, a dialogue
    and the front-end can all rewrite the current prompt without the game loop
    having to thread the new text back through every call."""

    DEFAULT = "What would you like to do?"

    def __init__(self, currentPrompt=DEFAULT):
        self.text = currentPrompt

    def reset(self):
        self.text = self.DEFAULT
