import json
import sys

import pytest
from jsonschema.exceptions import ValidationError

from tak.saves import validateAgainstSchema, syncBrowserSaves, getJsModule


def test_validateAgainstSchema_accepts_and_rejects(tmp_path):
    schema = tmp_path / "save.json"
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"loop": {"type": "integer", "minimum": 1}},
                "required": ["loop"],
            }
        )
    )
    validateAgainstSchema({"loop": 3}, str(schema))
    with pytest.raises(ValidationError):
        validateAgainstSchema({"loop": 0}, str(schema))
    with pytest.raises(ValidationError):
        validateAgainstSchema({}, str(schema))


def test_getJsModule_is_none_outside_a_browser(noJs):
    assert getJsModule() is None


def test_getJsModule_finds_an_injected_module(fakeJs):
    assert getJsModule() is fakeJs


def test_syncBrowserSaves_is_a_no_op_outside_a_browser(noJs):
    assert syncBrowserSaves() is False


def test_syncBrowserSaves_calls_the_worker_global(fakeJs):
    assert syncBrowserSaves() is True
    assert fakeJs.syncCallCount == 1


def test_syncBrowserSaves_without_the_global_is_a_no_op(fakeJs):
    fakeJs.syncSaves = None
    assert syncBrowserSaves() is False


def test_syncBrowserSaves_never_raises(fakeJs, capsys):
    def broken():
        raise RuntimeError("quota exceeded")

    fakeJs.syncSaves = broken
    assert syncBrowserSaves() is False
    assert "quota exceeded" in capsys.readouterr().out
