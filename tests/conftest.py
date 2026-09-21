import json
import sys

import pytest

from tak.prompt import Prompt

RING_SIZE = 8192


def makeHeader(chips=None, title=""):
    """A header provider for tests: returns the same header every time."""
    return lambda: {"title": title, "chips": list(chips or [])}


@pytest.fixture
def prompt():
    return Prompt()


class FakeAtomics:
    """The two Atomics operations the bridge uses, over a plain Python list."""

    @staticmethod
    def load(array, index):
        return array[index]

    @staticmethod
    def store(array, index, value):
        array[index] = value


class FakeJs:
    """Stand-in for Pyodide's `js` module and the globals game-worker.js sets.

    The ring buffer is modelled exactly as the real one: sabMeta[0] is the write
    index (advanced only by the browser), sabMeta[1] the read index (advanced
    only by Python), both monotonic and taken modulo the ring size.
    """

    def __init__(self, ringSize=RING_SIZE):
        self.Atomics = FakeAtomics
        self.sabMeta = [0, 0]
        self.sabData = bytearray(ringSize)
        self.sabRingSize = ringSize
        self.posted = []
        self.syncCallCount = 0

    def sendToMain(self, message):
        self.posted.append(message)

    def syncSaves(self):
        self.syncCallCount += 1

    def writePlayerInput(self, value):
        """Mirror of writeToRing() in boot.js."""
        self._writeToRing(json.dumps({"type": "input", "value": value}))

    def _writeToRing(self, text):
        payload = (text + "\n").encode("utf-8")
        writeIndex = self.sabMeta[0]
        for offset, byte in enumerate(payload):
            self.sabData[(writeIndex + offset) % self.sabRingSize] = byte
        self.sabMeta[0] = writeIndex + len(payload)


def restoreJs(previous):
    if previous is None:
        sys.modules.pop("js", None)
    else:
        sys.modules["js"] = previous


@pytest.fixture
def fakeJs():
    """Register a fake `js` module the way Pyodide would, and undo it after."""
    js = FakeJs()
    previous = sys.modules.get("js")
    sys.modules["js"] = js
    try:
        yield js
    finally:
        restoreJs(previous)


@pytest.fixture
def noJs():
    """Guarantee no `js` module is importable, as outside a browser."""
    previous = sys.modules.pop("js", None)
    try:
        yield
    finally:
        restoreJs(previous)
