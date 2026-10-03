"""Save export / import (assets/saves.js) and how both runtimes wire it in.

The import path is the one place the browser runtimes write a player's saves
from something other than the game itself, so it is checked twice: statically
here (the control is there, the runtimes stop every write of their own before
an import writes), and by running saves.js's validation under Node when Node
is installed (CI's runners have it). The full round trip in a real browser is
described in the README and was run by hand with Playwright.
"""

import json
import os
import shutil
import subprocess

import pytest

import tak.web
from tak.web import readAsset

RUNTIMES = (
    # page script, its IndexedDB write function, the save root it passes
    ("boot.js", "function idbWrite(files)", "root: saveDir"),
    ("console.js", "function keepFiles(files)", 'root: "/game"'),
)


def test_saves_js_ships_with_the_package():
    assert os.path.exists(tak.web.assetPath("saves.js"))
    assert "window.TakSaves" in readAsset("saves.js")


def test_the_control_offers_download_and_load():
    saves = readAsset("saves.js")
    for label in ('"Saves"', '"Download my saves"', '"Load saves from a file"'):
        assert label in saves, label
    # Phone-sized targets and text that does not make iOS zoom in.
    assert "min-height: 44px" in saves
    assert "font: 16px" in saves


def test_the_export_format_is_the_documented_one():
    saves = readAsset("saves.js")
    assert 'const FORMAT = "tak-saves";' in saves
    assert "const FORMAT_VERSION = 1;" in saves
    for key in ("format:", "version:", "game:", "exported:", "files:"):
        assert key in saves, key


@pytest.mark.parametrize("asset,writer,rootOption", RUNTIMES)
def test_each_runtime_loads_the_control(asset, writer, rootOption):
    page = readAsset(asset)
    assert 'config.savesUrl || "/tak/saves.js"' in page
    assert "window.TakSaves.attach({" in page
    assert rootOption in page
    assert "stop: stopForSaveTransfer" in page


@pytest.mark.parametrize("asset,writer,rootOption", RUNTIMES)
def test_an_import_stops_every_write_of_the_runtimes_own(asset, writer, rootOption):
    # The runtimes CLEAR the store on every sync, so once an import starts
    # nothing of theirs may write again until the page reloads: the flag is
    # checked on entry and again where the transaction is created, and the
    # Worker is terminated so it cannot post another sync.
    page = readAsset(asset)
    stop = page.split("function stopForSaveTransfer() {", 1)[1].split("}", 1)[0]
    assert "savesFrozen = true;" in stop
    assert "worker.terminate()" in stop
    body = page.split(writer, 1)[1]
    body = body[: body.index("\n    }\n")]
    assert body.count("if (savesFrozen)") == 2, body
    assert body.index("if (savesFrozen) { db.close(); return; }") < body.index(
        'db.transaction(IDB_STORE, "readwrite")'
    )


def test_an_import_never_clears_or_deletes_the_game_store():
    saves = readAsset("saves.js")
    merge = saves.split("async function mergeIntoStore(", 1)[1].split("\n  }\n", 1)[0]
    assert ".put(value, path)" in merge
    assert ".clear(" not in merge and ".delete(" not in merge
    # The only deletes in the file are of old backups, in their own database.
    assert saves.count(".delete(") == 1
    assert "tx.objectStore(BACKUP_STORE).delete(k)" in saves
    assert ".clear(" not in saves


def test_the_import_backs_up_before_it_writes_and_reloads_after():
    saves = readAsset("saves.js")
    apply = saves.split("async function applyImport(", 1)[1].split("\n    }\n", 1)[0]
    order = [
        apply.index("stopGame();"),
        apply.index('channel.postMessage({ type: "import" })'),
        apply.index("current = await readStore(idbName)"),
        apply.index("await writeBackup("),
        apply.index("await mergeIntoStore("),
        apply.index("location.reload()"),
    ]
    assert order == sorted(order)


# -- saves.js under Node ---------------------------------------------------------

NODE = shutil.which("node")

HARNESS = r"""
globalThis.window = globalThis;
require(process.argv[1]);
const S = globalThis.TakSaves;
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([text, game, root]) => {
  const r = S._parseImport(text, game, root);
  if (!r.ok) return { ok: false, reason: r.reason };
  const files = {};
  for (const [p, v] of r.files) files[p] = typeof v === "string" ? ["text", v] : ["bytes", Array.from(v)];
  return { ok: true, files };
});
// A round trip through buildExport: text stays text, bytes stay bytes.
const doc = S._buildExport("g-files", [["/game/a.txt", "héllo"], ["/game/b.bin", new Uint8Array([0, 255, 10, 128])]],
                           new Date("2026-10-03T12:00:00Z"));
const back = S._parseImport(JSON.stringify(doc), "g-files", "/game");
const cmp = S._compare(new Map([["/game/a.txt", "old"], ["/game/c.txt", "x"]]), back.files);
console.log(JSON.stringify({ out, doc, roundTrip: back.ok && S._sameValue(back.files.get("/game/b.bin"),
  new Uint8Array([0, 255, 10, 128])) && back.files.get("/game/a.txt") === "héllo", cmp }));
"""


def _doc(**overrides):
    doc = {
        "format": "tak-saves",
        "version": 1,
        "game": "tidewater-saves",
        "exported": "2026-10-03T12:00:00.000Z",
        "files": {"/saves/slot_1/save.json": '{"loop": 2}'},
    }
    doc.update(overrides)
    return json.dumps(doc)


def _run(cases):
    result = subprocess.run(
        [NODE, "-e", HARNESS, tak.web.assetPath("saves.js"), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_validation_accepts_a_good_file_and_refuses_everything_else():
    game, root = "tidewater-saves", "/saves"
    refused = {
        "not json": "{not json",
        "not a saves file": json.dumps({"hello": 1}),
        "another game": _doc(game="overwinter-saves"),
        "newer version": _doc(version=2),
        "no version": _doc(version="1"),
        "no files": _doc(files={}),
        "files is a list": _doc(files=[]),
        "outside the root": _doc(files={"/game/x": "1"}),
        "dot-dot": _doc(files={"/saves/../etc/passwd": "1"}),
        "root itself": _doc(files={"/saves": "1"}),
        "double slash": _doc(files={"/saves//x": "1"}),
        "prefix trick": _doc(files={"/savesx/a": "1"}),
        "backslash": _doc(files={"/saves/a\\b": "1"}),
        "number content": _doc(files={"/saves/a": 5}),
        "bad base64": _doc(files={"/saves/a": {"base64": "!!!"}}),
        "extra keys": _doc(files={"/saves/a": {"base64": "AA==", "x": 1}}),
        "a json array": "[]",
    }
    cases = [[_doc(), game, root]] + [[text, game, root] for text in refused.values()]
    results = _run(cases)["out"]
    assert results[0] == {
        "ok": True,
        "files": {"/saves/slot_1/save.json": ["text", '{"loop": 2}']},
    }
    for name, result in zip(refused, results[1:]):
        assert result["ok"] is False, name
        assert "Nothing was changed" in result["reason"], (name, result["reason"])
    reasons = dict(zip(refused, (r["reason"] for r in results[1:])))
    assert "another game (overwinter)" in reasons["another game"]
    assert "not this one (tidewater)" in reasons["another game"]
    assert "newer version" in reasons["newer version"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_export_is_binary_safe_and_compare_never_drops_a_file():
    result = _run([])
    doc = result["doc"]
    assert doc == {
        "format": "tak-saves",
        "version": 1,
        "game": "g-files",
        "exported": "2026-10-03T12:00:00.000Z",
        "files": {"/game/a.txt": "héllo", "/game/b.bin": {"base64": "AP8KgA=="}},
    }
    assert result["roundTrip"] is True
    assert result["cmp"] == {
        "added": ["/game/b.bin"],
        "replaced": ["/game/a.txt"],
        "same": [],
        "kept": ["/game/c.txt"],
    }
