"""tak.arcade: scores and achievement unlocks for games on arcade (RFC 0014)."""

import json

from tak import arcade


class Recorder:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def __call__(self, text):
        if self.fail:
            raise RuntimeError("postMessage failed")
        self.requests.append(json.loads(text))


def test_outside_a_browser_both_calls_are_no_ops(noJs):
    assert arcade.submitScore("most-money", 12450) is None
    assert arcade.unlock("first-catch") is None


def test_without_the_worker_global_both_calls_are_no_ops(fakeJs):
    # An older game-worker.js, or the console runtime: no takArcade.
    assert arcade.submitScore("most-money", 1) is None
    assert arcade.unlock("first-catch") is None


def test_a_score_is_posted_to_the_page_as_json(fakeJs):
    fakeJs.takArcade = Recorder()
    arcade.submitScore("most-money", 12450)
    arcade.submitScore("fastest-crossing", 41.7, run="seed-12:3")
    assert fakeJs.takArcade.requests == [
        {"kind": "score", "board": "most-money", "value": 12450},
        {
            "kind": "score",
            "board": "fastest-crossing",
            "value": 41.7,
            "run": "seed-12:3",
        },
    ]


def test_an_unlock_is_posted_to_the_page(fakeJs):
    fakeJs.takArcade = Recorder()
    arcade.unlock("first-catch")
    arcade.unlock("first-catch")  # idempotent at the service; the client just sends
    assert fakeJs.takArcade.requests == [
        {"kind": "unlock", "achievement": "first-catch"},
        {"kind": "unlock", "achievement": "first-catch"},
    ]


def test_malformed_calls_are_ignored_not_raised(fakeJs):
    fakeJs.takArcade = Recorder()
    for board, value in [
        ("Most Money", 1),
        ("m", 1),
        ("../admin", 1),
        (None, 1),
        ("most-money", "12"),
        ("most-money", True),
        ("most-money", float("nan")),
        ("most-money", float("inf")),
        ("most-money", 10**400),
        ("most-money", None),
    ]:
        assert arcade.submitScore(board, value) is None
    assert arcade.submitScore("most-money", 1, run="has spaces") is None
    assert arcade.submitScore("most-money", 1, run=5) is None
    assert arcade.unlock("First Catch") is None
    assert arcade.unlock(7) is None
    assert fakeJs.takArcade.requests == []


def test_a_failing_page_never_reaches_the_game(fakeJs):
    fakeJs.takArcade = Recorder(fail=True)
    assert arcade.submitScore("most-money", 1) is None
    assert arcade.unlock("first-catch") is None
