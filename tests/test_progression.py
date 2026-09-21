import pytest

from tak import Progression


def unlocks():
    return [
        {
            "id": "shop",
            "name": "the shop",
            "announcement": "Go sell.",
            "condition": lambda s: s["fish"] >= 1,
        },
        {
            "id": "home",
            "name": "home",
            "announcement": "Sleep.",
            "condition": lambda s: s["money"] > 0,
        },
        {
            "id": "bank",
            "name": "the bank",
            "announcement": "Bank it.",
            "condition": lambda s: s["money"] >= 150,
        },
    ]


def test_a_brand_new_player_has_unlocked_nothing():
    progression = Progression(unlocks())
    granted = []
    assert progression.getNextUnlock({"fish": 0, "money": 0}, granted) is None
    assert granted == []
    assert not progression.isUnlocked(granted, "shop")


def test_the_first_earned_unlock_is_granted_and_announced():
    progression = Progression(unlocks())
    granted = []
    unlock = progression.getNextUnlock({"fish": 3, "money": 0}, granted)
    assert unlock["id"] == "shop"
    assert unlock["announcement"] == "Go sell."
    assert granted == ["shop"]
    assert progression.isUnlocked(granted, "shop")


def test_an_unlock_is_only_announced_once():
    progression = Progression(unlocks())
    granted = []
    progression.getNextUnlock({"fish": 3, "money": 0}, granted)
    assert progression.getNextUnlock({"fish": 3, "money": 0}, granted) is None
    assert granted == ["shop"]


def test_only_one_feature_is_granted_per_call():
    progression = Progression(unlocks())
    granted = []
    state = {"fish": 1, "money": 500}  # all three earned at once
    assert progression.getNextUnlock(state, granted)["id"] == "shop"
    assert progression.getNextUnlock(state, granted)["id"] == "home"
    assert progression.getNextUnlock(state, granted)["id"] == "bank"
    assert progression.getNextUnlock(state, granted) is None


def test_catchUp_grants_everything_earned_without_announcing():
    progression = Progression(unlocks())
    granted = []
    progression.catchUp({"fish": 1, "money": 500}, granted)
    assert granted == ["shop", "home", "bank"]


def test_catchUp_grants_nothing_to_a_new_game():
    progression = Progression(unlocks())
    granted = []
    progression.catchUp({"fish": 0, "money": 0}, granted)
    assert granted == []


def test_unlockAll_replaces_the_granted_list_in_place():
    progression = Progression(unlocks())
    granted = ["shop"]
    same = granted
    progression.unlockAll(granted)
    assert same == ["shop", "home", "bank"]
    assert progression.allFeatureIds == ["shop", "home", "bank"]


def test_the_granted_list_the_game_owns_is_the_one_written_to():
    # The whole point: a game passes its saved list and it is mutated in place,
    # so what is announced is exactly what is persisted.
    progression = Progression(unlocks())
    save = {"unlockedFeatures": []}
    progression.getNextUnlock({"fish": 1, "money": 0}, save["unlockedFeatures"])
    assert save == {"unlockedFeatures": ["shop"]}


@pytest.mark.parametrize(
    "bad",
    [
        [{"id": "x", "name": "x", "announcement": "x"}],
        [{"id": "x", "name": "x", "announcement": "x", "condition": "not callable"}],
        [
            {"id": "x", "name": "x", "announcement": "x", "condition": lambda s: True},
            {"id": "x", "name": "y", "announcement": "y", "condition": lambda s: True},
        ],
    ],
)
def test_malformed_unlock_tables_are_rejected_at_construction(bad):
    with pytest.raises(ValueError):
        Progression(bad)
