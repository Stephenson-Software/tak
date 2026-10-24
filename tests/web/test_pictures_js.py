"""The browser side of setPicture: client.js draws a screen's picture, keeping
one blob: URL per key, and the Pyodide runtime moves each picture's bytes from
the Worker to the page once.

client.js runs under Node (CI's runners have it) against a tiny stand-in for
the DOM; nothing is fetched.
"""

import json
import shutil
import subprocess

import pytest

from tak.web import assetPath, readAsset

NODE = shutil.which("node")

HARNESS = r"""
function node(tag) {
  const n = { tagName: tag.toUpperCase(), kids: [], className: "" };
  n.append = (...k) => { n.kids.push(...k); };
  return n;
}
const app = node("div");
Object.defineProperty(app, "innerHTML", { set() { app.kids = []; }, get() { return ""; } });
globalThis.window = globalThis;
globalThis.document = {
  getElementById: () => app,
  createElement: node,
  addEventListener: () => {},
  title: "",
};
let made = 0;
URL.createObjectURL = (blob) => { made++; return "blob:test/" + made; };
require(process.argv[2]);
const C = globalThis.TakClient;
const images = () => app.kids.filter((k) => k.tagName === "IMG")
  .map((k) => ({ src: k.src, alt: k.alt, className: k.className }));
const screen = (picture) => ({ type: "dialogue", text: "x", header: { chips: [] }, picture });
const out = {};

// Pyodide-style host: no pictureUrl, bytes arrive through addPicture.
C.init(() => {});
C.render(screen({ key: "strip", alt: "The strip." }));
out.beforeBytes = images();
C.addPicture("strip", new Uint8Array([137, 80, 78, 71]));
C.addPicture("strip", new Uint8Array([137, 80, 78, 71]));  // a repeat is ignored
C.render(screen({ key: "strip", alt: "The strip." }));
C.render(screen({ key: "strip", alt: "The strip." }));
out.afterBytes = images();
out.blobsMade = made;
C.render({ type: "dialogue", text: "no picture", header: { chips: [] } });
out.noPicture = images();

// Server-style host: pictures come from a URL.
C.init(() => {}, { pictureUrl: (key) => "/picture/" + key });
C.render(screen({ key: "galley", alt: "The galley." }));
out.served = images();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_client_draws_a_picture_from_bytes_or_url(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    result = subprocess.run(
        [NODE, str(harness), assetPath("client.js")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout.strip().splitlines()[-1])
    # No bytes yet and no URL to fetch: nothing is drawn (not a broken image).
    assert out["beforeBytes"] == []
    assert out["afterBytes"] == [
        {"src": "blob:test/1", "alt": "The strip.", "className": "picture"}
    ]
    assert out["blobsMade"] == 1  # one blob: URL per key, however often it is drawn
    assert out["noPicture"] == []
    assert out["served"] == [
        {"src": "/picture/galley", "alt": "The galley.", "className": "picture"}
    ]


def test_the_worker_reads_pictures_from_the_bundle_and_transfers_them():
    worker = readAsset("game-worker.js")
    assert "globalThis.sendPicture" in worker
    assert "/game/${picturesDir}/${key}.png" in worker
    assert "self.postMessage({ type: 'picture', key, bytes }, [bytes.buffer])" in worker
    # Installed before the game's Python runs.
    assert worker.index("globalThis.sendPicture") < worker.index(
        "pyodide.runPythonAsync"
    )


def test_boot_hands_pictures_to_the_client_and_tells_the_worker_where_they_are():
    boot = readAsset("boot.js")
    assert 'message.type === "picture"' in boot
    assert "TakClient.addPicture(message.key, message.bytes)" in boot
    assert 'picturesDir: config.picturesDir || "pictures"' in boot


def test_the_stylesheet_fits_a_picture_to_the_column():
    assert "img.picture" in readAsset("client.css")
