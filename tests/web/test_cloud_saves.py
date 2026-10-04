"""Cloud saves (assets/cloud.js, Stephenson-Software RFC 0016).

cloud.js's engine is run under Node when Node is installed (CI's runners
have it): the canonical unit encoding is checked against the server's
(arcade-social hashes compact, key-sorted JSON in UTF-8), the per-unit merge
against the RFC's table, the "an error is never an empty cloud" rule, and the
randomized three-device test (tests/web/cloud_sim.js) against an in-memory
server with arcade-social's upload rules. The wiring in boot.js and saves.js
is checked statically.
"""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

import tak.web
from tak.web import readAsset

NODE = shutil.which("node")
HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "cloud_sim.js")
SEEDS = os.environ.get("TAK_CLOUD_SIM_SEEDS", "300")
# Seeds (and history lengths) that each found a real gap in an earlier cloud.js:
# a new game started in a lost slot (242), a lost slot never restored (262), a
# loss while the cloud was down (361), a loss while uploads were paused (1643).
FIXED_SEEDS = "242:200,262:200,361:200,1643:300"
STEPS = os.environ.get("TAK_CLOUD_SIM_STEPS", "200")
needsNode = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(script, *arguments):
    result = subprocess.run(
        [NODE, "-e", script, tak.web.assetPath("cloud.js")]
        + [json.dumps(a) for a in arguments],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


PRELUDE = r"""
globalThis.window = globalThis;
require(process.argv[1]);
const C = globalThis.TakCloud;
const arg = (i) => JSON.parse(process.argv[2 + i]);
"""


def _serverCanonical(unitFiles):
    """arcade-social's saves.canonical(): what the server hashes."""
    return json.dumps(
        unitFiles, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


# --- static wiring ---------------------------------------------------------------------


def test_cloud_js_ships_with_the_package():
    assert os.path.exists(tak.web.assetPath("cloud.js"))
    assert "window.TakCloud" in readAsset("cloud.js")


def test_boot_loads_cloud_saves_only_when_the_game_opts_in_on_arcade():
    boot = readAsset("boot.js")
    assert "const cloudWanted = config.cloudSaves === true && onArcadeHost();" in boot
    assert 'config.cloudUrl || "/tak/cloud.js"' in boot
    host = boot.split("function onArcadeHost() {", 1)[1].split("\n  }\n", 1)[0]
    assert 'location.protocol === "https:"' in host
    assert r"\.play\.danielstephenson\.dev$" in host


def test_the_game_waits_for_the_cloud_only_in_a_browser_that_opted_in():
    boot = readAsset("boot.js")
    preStart = boot.split("function cloudPreStart() {", 1)[1].split("\n    }\n", 1)[0]
    assert (
        'localStorage.getItem("tak-cloud:" + idbName + ":enrolled") === "1"' in preStart
    )
    assert "if (!optedIn) go();" in preStart
    assert "setTimeout(go, 4000)" in preStart
    # A pull may only begin before the Worker starts; after that, never.
    assert "if (decided) return false; decided = true; return true;" in preStart
    assert "if (startGate) startGate.then(startWorker);" in boot
    assert "else startWorker();" in boot


def test_uploads_trail_only_committed_saves_and_never_follow_a_failed_restore():
    boot = readAsset("boot.js")
    write = boot.split("function idbWrite(files, deleted) {", 1)[1].split(
        "\n    }\n", 1
    )[0]
    assert (
        "tx.oncomplete = () => { db.close(); if (cloud) cloud.committed(deleted); };"
        in write
    )
    assert "cloud.committed" not in write.replace(
        "tx.oncomplete = () => { db.close(); if (cloud) cloud.committed(deleted); };",
        "",
    )
    nosave = boot.split('if (message.type === "nosave") {', 1)[1].split("return;", 1)[0]
    assert "cloud.restoreFailed()" in nosave
    stop = boot.split("function stopForSaveTransfer() {", 1)[1].split("}", 1)[0]
    assert "cloud.stopped()" in stop


def test_a_pull_is_the_imports_own_put_only_path():
    cloud = readAsset("cloud.js")
    pull = cloud.split("pull: async (files, expected) => {", 1)[1].split(
        "\n      },\n", 1
    )[0]
    # Validated by the import's own parser, other tabs stopped, a backup kept
    # and read back (writeBackup), put-only (mergeIntoStore), read back.
    order = [
        "S.parseImport(",
        "onStopOtherTabs",
        "S.readStore(idbName)",
        'S.writeBackup(idbName, S.buildExport(idbName, current), "cloud")',
        "S.mergeIntoStore(idbName, parsed.files)",
        "did not read back",
        "went missing",
    ]
    positions = [pull.index(step) for step in order]
    assert positions == sorted(positions), positions
    # It refuses to write if the store moved since the merge was computed.
    assert pull.index("nothing was written") < pull.index("S.writeBackup(")
    # Nothing in cloud.js ever deletes from a store or sends a `deleted` list.
    assert ".delete(" not in cloud
    assert "deleted:" not in cloud


def test_backups_keep_their_newest_five_per_kind():
    saves = readAsset("saves.js")
    write = saves.split("async function writeBackup(idbName, doc, kind) {", 1)[1].split(
        "\n  }\n", 1
    )[0]
    assert '"cloud:" + doc.exported' in write
    assert ".filter((k) => backupKind(k) === kind)" in write
    assert "TakSaves" in saves and "internals: {" in saves


# --- under Node ----------------------------------------------------------------------------


VECTORS = [
    {"/saves/slot_1/save.json": '{"loop": 2, "name": "Ann"}'},
    {"/saves/slot_2/b.json": "z", "/saves/slot_2/a.json": 'line\nnext\ttab "q" \\ /'},
    {"/saves/slot_3/x": "café     \u007f \u0001 \u001f \U0001f600"},
    {"/game/notes.txt": {"base64": "AAEC/w=="}, "/game/a.txt": "é"},
    {"/saves/\U0001f600/a": "astral key", "/saves/～/a": "bmp key above it"},
]


@needsNode
def test_the_canonical_encoding_matches_the_servers():
    script = PRELUDE + r"""
(async () => {
  const out = [];
  for (const v of arg(0)) out.push([C._canonical(v), await C._sha256(C._canonical(v))]);
  console.log(JSON.stringify(out));
})();
"""
    results = _node(script, VECTORS)
    for vector, (text, digest) in zip(VECTORS, results):
        expected = _serverCanonical(vector)
        assert text.encode("utf-8") == expected, vector
        assert digest == hashlib.sha256(expected).hexdigest()


@needsNode
def test_the_merge_keeps_both_copies_and_never_takes_the_newest():
    script = PRELUDE + r"""
(async () => {
  const f = (slot, v) => ({ ["/saves/" + slot + "/save.json"]: v });
  const u = (pairs) => Object.fromEntries(pairs.map(([s, v]) => [s, f(s, v)]));
  const cases = {
    onlyLocal: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "b"]]), u([["slot_1", "l"]]), "/saves"),
    onlyCloud: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "h"]]), u([["slot_1", "b"]]), "/saves"),
    both: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "h"], ["slot_2", "x"]]), u([["slot_1", "l"]]), "/saves"),
    same: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "s"]]), u([["slot_1", "s"]]), "/saves"),
    changeBeatsRemoval: await C._merge3(u([["slot_1", "b"]]), u([]), u([["slot_1", "l"]]), "/saves"),
    removedHereChangedThere: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "h"]]), u([]), "/saves"),
    removedHere: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "b"]]), u([]), "/saves"),
    alreadyKept: await C._merge3(u([["slot_1", "b"]]), u([["slot_1", "h"], ["slot_5", "l"]]), u([["slot_1", "l"]]), "/saves"),
  };
  let unresolvable = null;
  try { await C._merge3({ "x": { "/game/x": "b" } }, { "x": { "/game/x": "h" } }, { "x": { "/game/x": "l" } }, "/game"); }
  catch (e) { unresolvable = e instanceof C._Unresolvable; }
  console.log(JSON.stringify({ cases, unresolvable }));
})();
"""
    out = _node(script)
    cases = out["cases"]

    def flat(case):
        return dict(
            (name, list(files.values())[0]) for name, files in case["merged"].items()
        )

    assert flat(cases["onlyLocal"]) == {"slot_1": "l"}
    assert flat(cases["onlyCloud"]) == {"slot_1": "h"}
    assert flat(cases["both"]) == {"slot_1": "h", "slot_2": "x", "slot_3": "l"}
    assert cases["both"]["kept"] == ["slot_3"]
    assert cases["both"]["merged"]["slot_3"] == {"/saves/slot_3/save.json": "l"}
    assert flat(cases["same"]) == {"slot_1": "s"}
    assert flat(cases["changeBeatsRemoval"]) == {"slot_1": "l"}
    assert flat(cases["removedHereChangedThere"]) == {"slot_1": "h"}
    assert flat(cases["removedHere"]) == {}
    assert flat(cases["alreadyKept"]) == {"slot_1": "h", "slot_5": "l"}
    assert out["unresolvable"] is True


@needsNode
@pytest.mark.parametrize("code", [0, 401, 403, 404, 409, 413, 422, 429, 500, 503])
def test_an_unknown_cloud_is_never_read_as_empty(code):
    script = PRELUDE + r"""
(async () => {
  const calls = [];
  const store = { "/saves/slot_1/save.json": "mine" };
  const before = JSON.stringify(store);
  const engine = C._createEngine({
    store: "g-saves", root: "/saves", device: "device-0001", label: "t", pullEnabled: true,
    api: {
      status: async () => { calls.push("status"); return { status: arg(0), body: arg(0) === 0 ? null : { saves: "x" } }; },
      upload: async () => { calls.push("upload"); return { status: 201, body: { id: 1 } }; },
      version: async () => { calls.push("version"); return { status: 200, body: { files: {} } }; },
    },
    state: { load: () => ({ sync: { id: 3, units: {} } }), save: () => {} },
    local: { read: async () => Object.assign({}, store), pull: async () => { calls.push("pull"); } },
  });
  const outcome = await engine.load(() => true);
  console.log(JSON.stringify({ outcome, calls, same: JSON.stringify(store) === before }));
})();
"""
    out = _node(script, code)
    assert out["outcome"] == "unknown"
    assert out["calls"] == ["status"]
    assert out["same"] is True


@needsNode
def test_a_failed_restore_session_uploads_nothing():
    script = PRELUDE + r"""
(async () => {
  const calls = [];
  const engine = C._createEngine({
    store: "g-saves", root: "/saves", device: "device-0001", label: "t", pullEnabled: true,
    api: { status: async () => { calls.push("status"); return { status: 200, body: { enrolled: true, writable: true, pull: true, head: null } }; },
           upload: async () => { calls.push("upload"); return { status: 201, body: { id: 1 } }; },
           version: async () => ({ status: 404, body: null }) },
    state: { load: () => null, save: () => {} },
    local: { read: async () => ({ "/saves/slot_1/save.json": "x" }), pull: async () => {} },
  });
  engine.disable("restore-failed");
  console.log(JSON.stringify({ outcome: await engine.afterSave(), calls }));
})();
"""
    out = _node(script)
    assert out == {"outcome": "disabled", "calls": []}


@needsNode
def test_randomized_three_devices_never_lose_a_committed_save():
    result = subprocess.run(
        [NODE, SIM, tak.web.assetPath("cloud.js"), SEEDS, STEPS],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    assert result.returncode == 0 and result.stdout.startswith("ok "), (
        result.stdout[-4000:] + result.stderr[-2000:]
    )
