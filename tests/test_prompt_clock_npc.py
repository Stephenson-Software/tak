import pytest

from tak import Prompt, NPC, formatHour


def test_prompt_holds_and_resets_its_text():
    prompt = Prompt("Welcome.")
    assert prompt.text == "Welcome."
    prompt.text = "Try again!"
    prompt.reset()
    assert prompt.text == Prompt.DEFAULT


def test_formatHour_covers_the_whole_day():
    assert formatHour(0) == "12:00 AM"
    assert formatHour(8) == "8:00 AM"
    assert formatHour(12) == "12:00 PM"
    assert formatHour(23) == "11:00 PM"


@pytest.mark.parametrize("bad", [24, -1, "8", None])
def test_formatHour_rejects_anything_off_the_clock(bad):
    with pytest.raises(ValueError):
        formatHour(bad)


def test_npc_introduce_and_options():
    npc = NPC(
        "Gilbert", "Runs the shop.", [{"question": "Prices?", "response": "Fair."}]
    )
    assert npc.introduce() == "Gilbert: Runs the shop."
    assert [o["question"] for o in npc.get_dialogue_options()] == ["Prices?"]
    assert npc.get_dialogue_response(0) == "Fair."
    assert npc.get_dialogue_response(5) == ""
    assert npc.get_dialogue_response(-1) == ""


def test_npc_without_options_has_an_empty_list():
    assert NPC("Tom", "Old.").get_dialogue_options() == []
    assert NPC("Tom", "Old.", []).dialogue_options == []


def test_npc_response_may_be_a_callable_read_on_demand():
    state = {"loops": 1}
    npc = NPC(
        "Sam", "", [{"question": "?", "response": lambda: "Loop %d" % state["loops"]}]
    )
    assert npc.get_dialogue_response(0) == "Loop 1"
    state["loops"] = 4
    assert npc.get_dialogue_response(0) == "Loop 4"


def test_npc_hides_options_whose_condition_is_unmet_and_indexes_the_visible_ones():
    known = set()
    npc = NPC(
        "Margaret",
        "",
        [
            {"question": "Hello", "response": "Hello."},
            {
                "question": "The ledger?",
                "response": "How do you know?",
                "condition": lambda: "ledger" in known,
            },
            {"question": "Bye", "response": "Bye."},
        ],
    )
    assert [o["question"] for o in npc.get_dialogue_options()] == ["Hello", "Bye"]
    # Index 1 is "Bye" while the middle option is hidden - not the hidden one.
    assert npc.get_dialogue_response(1) == "Bye."
    known.add("ledger")
    assert [o["question"] for o in npc.get_dialogue_options()] == [
        "Hello",
        "The ledger?",
        "Bye",
    ]
    assert npc.get_dialogue_response(1) == "How do you know?"
