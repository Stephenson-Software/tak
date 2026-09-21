# @author Daniel McCoy Stephenson
"""Progressive disclosure: which parts of the game the player has been shown.

A new game opens with one thing to do, and every other option arrives later,
one at a time, as the player earns it. The point is pacing rather than
difficulty: nothing here makes an action harder, it only decides whether the
option is on the menu yet, so a first-time player is never handed eleven
choices they have no context for. Each unlock announces itself with the reason
it appeared, so the next thing to do is always the thing that just became
possible.

Data-driven: the game hands a Progression a list of unlocks, each a dict with

    id            a short stable string the game's menus test for
    name          what it is, for messages ("the shop")
    announcement  the line shown when it arrives
    condition     a one-argument callable taking the game's state object

and the set of already-granted ids is a plain list the game owns and saves
(``granted`` below) - so an unlock is announced exactly once per run and stays
available for good, and a game decides for itself what "state" is. In a time
loop that list lives with the knowledge that survives the reset, which is what
makes a feature stay unlocked from one loop to the next.
"""

REQUIRED_KEYS = ("id", "name", "announcement", "condition")


class Progression:
    def __init__(self, unlocks):
        seen = set()
        for unlock in unlocks:
            missing = [key for key in REQUIRED_KEYS if key not in unlock]
            if missing:
                raise ValueError(
                    "unlock %r is missing %s" % (unlock.get("id"), ", ".join(missing))
                )
            if not callable(unlock["condition"]):
                raise ValueError("unlock %r: condition must be callable" % unlock["id"])
            if unlock["id"] in seen:
                raise ValueError("unlock id %r is listed twice" % unlock["id"])
            seen.add(unlock["id"])
        self.unlocks = list(unlocks)

    @property
    def allFeatureIds(self):
        return [unlock["id"] for unlock in self.unlocks]

    def isUnlocked(self, granted, featureId):
        """Whether the player has been shown the given feature yet."""
        return featureId in granted

    def getNextUnlock(self, state, granted):
        """Grant and return the next feature the player has earned, or None.

        One per call, deliberately, even when several conditions came true at
        once: announcing them all on one screen would hand the player the wall
        of new options this module exists to avoid. The rest are still earned
        and arrive on the following screens, one per action.

        The granted id is appended to ``granted`` in place - the game's own
        (saved) list - so the persisted list doubles as the already-announced
        flag."""
        for unlock in self.unlocks:
            if unlock["id"] in granted:
                continue
            if unlock["condition"](state):
                granted.append(unlock["id"])
                return unlock
        return None

    def catchUp(self, state, granted):
        """Grant every already-earned unlock at once, announcing nothing.

        For loading a game, where the drip above would be wrong: a save from
        before an unlock existed may already meet its condition, and handing it
        back one button per action would be worse than re-locking it."""
        while self.getNextUnlock(state, granted) is not None:
            pass

    def unlockAll(self, granted):
        """Grant every feature at once, in place. For tests and for anything
        that needs the full menu without playing through to it."""
        granted[:] = list(self.allFeatureIds)
