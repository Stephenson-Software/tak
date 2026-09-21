# @author Daniel McCoy Stephenson
class NPC:
    """A character the player can talk to.

    dialogue_options is a list of dicts, each with a "question" (what the
    player can ask) and a "response" (what comes back - a string, or a zero-arg
    callable evaluated on demand so the answer can reflect the current game
    state). An option may also carry a "condition": a zero-arg callable, and the
    option stays hidden while it returns False. That is how a character unlocks
    new lines as the game goes on - in a time loop, as the player learns things
    - instead of every question being visible from the first conversation."""

    def __init__(self, name: str, backstory: str, dialogue_options: list = None):
        self.name = name
        self.backstory = backstory
        if dialogue_options is None:
            self.dialogue_options = []
        else:
            self.dialogue_options = dialogue_options

    def introduce(self):
        """Returns the NPC's introduction text"""
        return f"{self.name}: {self.backstory}"

    def get_dialogue_options(self):
        """Returns the dialogue options currently available."""
        return [
            option for option in self.dialogue_options if self._is_available(option)
        ]

    def get_dialogue_response(self, option_index: int):
        """Returns the response for a specific dialogue option.

        The index is into the *currently available* options - the same list
        get_dialogue_options() returns and the front-ends number their menus
        from - so conditional options never shift a response onto the wrong
        question."""
        options = self.get_dialogue_options()
        if 0 <= option_index < len(options):
            response = options[option_index].get("response", "")
            if callable(response):
                return response()
            return response
        return ""

    def _is_available(self, option):
        condition = option.get("condition")
        if condition is None:
            return True
        return bool(condition())
