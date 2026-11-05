"""The README is the only reference a game author has for the public surface.

These tests fail when a keyword or a primitive is added without a line there,
and when the README's own saves snippet stops being safe to copy.
"""

import inspect
import os
import re

from tak.ui import BaseUserInterface, createUserInterface

ROOT = os.path.join(os.path.dirname(__file__), "..")


def readme():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        return f.read()


def test_every_create_user_interface_keyword_is_documented():
    parameters = inspect.signature(createUserInterface).parameters
    text = readme()
    for name, parameter in parameters.items():
        if parameter.default is inspect.Parameter.empty or name == "header":
            continue  # the required two and header are in the signature shown
        assert "`%s`" % name in text, name


def test_every_abstract_primitive_is_documented():
    text = readme()
    for name in BaseUserInterface.__abstractmethods__:
        assert "`%s(" % name in text, name


def test_the_saves_snippet_does_not_index_into_the_save():
    # readMetadata must not raise, and a save without the field reaches it.
    match = re.search(r"readMetadata=lambda slotPath, data: (.+)\)$", readme(), re.M)
    assert match, "README.md's saves snippet no longer shows readMetadata"
    assert 'data["' not in match.group(1)
