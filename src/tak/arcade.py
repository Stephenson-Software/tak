# @author Daniel McCoy Stephenson
"""High scores and achievements for a tak game played on arcade.

Stephenson-Software RFC 0014 section 2. A game reports to the boards and
achievements it declared (in the gateway's ``config/play/boards.yaml``):

    from tak import arcade

    arcade.submitScore("most-money", 12450)   # returns None; never raises
    arcade.unlock("first-catch")              # idempotent; never raises

Both calls are fire-and-forget. They never raise into the game and never wait
for the network:

- Under Pyodide, game-worker.js installs a ``takArcade`` global that posts the
  request to the page (postMessage is synchronous from the Worker's side, so
  this works while Python is mid-call, the same way ``syncSaves`` does). The
  page side, ``/tak/arcade.js`` (loaded by boot.js on the first request), sends
  it to arcade-social at https://api.play.danielstephenson.dev from the main
  thread, with the player's sign-in cookie. It does nothing when the page is
  not a ``https://<slug>.play.danielstephenson.dev`` game page, when the player
  is not signed in, or when the service is unreachable (one retry, then the
  request is dropped: a lost score is acceptable, a stalled game is not).
- Anywhere else (the console and HTTP front-ends, tests) both calls are no-ops,
  so a game calls them unconditionally.

The client does not filter scores: it sends every submission and the service
keeps each player's best. Scores are reported by the player's own browser and
can be forged; the service labels every board "not verified".
"""

import json
import math
import re

from tak.saves.browser import getJsModule

# Board and achievement ids, as arcade-social declares them.
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
# An optional opaque tag for a run, stored only in the service's log.
RUN_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _isId(value):
    return isinstance(value, str) and ID_PATTERN.match(value) is not None


def _isFiniteNumber(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def _post(request):
    """Hand one request to the page; True if it was handed over."""
    js = getJsModule()
    if js is None:
        return False
    takArcade = getattr(js, "takArcade", None)
    if takArcade is None:
        return False
    try:
        # A JSON string, so nothing depends on how Pyodide converts a dict.
        takArcade(json.dumps(request))
        return True
    except Exception:
        return False


def submitScore(board, value, run=None):
    """Report ``value`` on ``board`` for the signed-in player, if any.

    ``board`` is a declared board id; ``value`` a finite int or float (the
    service checks it against the board's limits). ``run`` is an optional tag
    for the run, 1-64 of ``A-Za-z0-9._:-``. Anything malformed is ignored.
    Returns None and never raises.
    """
    try:
        if not _isId(board) or not _isFiniteNumber(value):
            return None
        request = {"kind": "score", "board": board, "value": value}
        if run is not None:
            if not isinstance(run, str) or RUN_PATTERN.match(run) is None:
                return None
            request["run"] = run
        _post(request)
    except Exception:
        pass
    return None


def unlock(achievementId):
    """Unlock a declared achievement for the signed-in player, if any.

    Idempotent: unlocking one the player already holds changes nothing.
    Returns None and never raises.
    """
    try:
        if _isId(achievementId):
            _post({"kind": "unlock", "achievement": achievementId})
    except Exception:
        pass
    return None
