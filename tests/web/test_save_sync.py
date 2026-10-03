"""A runtime's save sync never deletes a stored save it did not see deleted.

Both browser runtimes mirror the Worker's save directory into IndexedDB. They
used to clear the store and rewrite it from the Worker's copy on every sync,
so a stored save that was missing from that copy - for whatever reason - was
erased by the next save. Now the page writes what was sent and deletes only
the paths the Worker names, and the Worker names only files this session had
and the game then removed.

These run the real functions from the shipped assets under Node, against a
fake object store and a fake Emscripten FS (CI's runners have Node).
"""

import json
import re
import shutil
import subprocess

import pytest

import tak.web
from tak.web import readAsset

NODE = shutil.which("node")
needsNode = pytest.mark.skipif(NODE is None, reason="node is not installed")


def extract(asset, signature, indent):
    """The source of one function in an asset, from its signature to the
    closing brace at the same indent."""
    source = readAsset(asset)
    start = source.index(signature)
    end = source.index("\n" + indent + "}\n", start)
    return source[start : end + len(indent) + 2]


def runNode(script):
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


FAKE_STORE = r"""
// An object store inside one transaction: writes are held until commit(),
// which applies all of them unless the transaction was aborted (as IndexedDB
// does). failOn: a key whose put throws, as a value IndexedDB cannot store.
function fakeStore(entries, failOn) {
  const data = new Map(entries);
  const pending = [];
  const store = {
    data, aborted: false,
    put(value, key) {
      if (key === failOn) throw new Error("DataCloneError");
      pending.push(["put", key, value]);
    },
    delete(key) { pending.push(["delete", key]); },
    clear() { throw new Error("the store must never be cleared"); },
    transaction: { abort() { store.aborted = true; } },
    commit() {
      if (!store.aborted) for (const [op, key, value] of pending) {
        if (op === "put") data.set(key, value); else data.delete(key);
      }
      pending.length = 0;
    },
  };
  return store;
}
const quiet = "[test]";
const asObject = (store) => { store.commit(); return Object.fromEntries([...store.data].sort()); };
"""


@needsNode
@pytest.mark.parametrize("asset", ["boot.js", "console.js"])
def test_the_page_deletes_only_what_the_worker_names(asset):
    mirror = extract(asset, "function mirrorToStore(", "  ")
    out = runNode(
        FAKE_STORE
        + mirror
        + r"""
const stored = [["/saves/slot_1/save.json", "one"], ["/saves/slot_2/save.json", "two"],
                ["/saves/slot_3/save.json", "three"]];
const results = {};
// The Worker's copy holds only slot 1 (slots 2 and 3 never reached it): both
// are kept, because it names neither as deleted.
let s = fakeStore(stored);
mirrorToStore(s, { "/saves/slot_1/save.json": "one, later" }, [], quiet);
results.missingKept = asObject(s);
// The game deleted slot 2: only slot 2 goes.
s = fakeStore(stored);
mirrorToStore(s, { "/saves/slot_1/save.json": "one" }, ["/saves/slot_2/save.json"], quiet);
results.namedDeleted = asObject(s);
// A Worker from an older tak sends no list: nothing is deleted.
s = fakeStore(stored);
mirrorToStore(s, {}, undefined, quiet);
results.noList = asObject(s);
// A path both sent and named is written, not deleted.
s = fakeStore(stored);
mirrorToStore(s, { "/saves/slot_2/save.json": "two again" }, ["/saves/slot_2/save.json"], quiet);
results.sentWins = asObject(s);
// All or nothing: a put that throws part-way leaves the store as it was,
// including the writes and deletes that came before and after it.
s = fakeStore(stored, "/saves/slot_2/save.json");
results.failedReturned = mirrorToStore(s, { "/saves/slot_1/save.json": "one, later",
  "/saves/slot_2/save.json": "bad", "/saves/slot_4/save.json": "four" }, ["/saves/slot_3/save.json"], quiet);
results.failedAborted = s.aborted;
results.failed = asObject(s);
console.log(JSON.stringify(results));
"""
    )
    assert out["missingKept"] == {
        "/saves/slot_1/save.json": "one, later",
        "/saves/slot_2/save.json": "two",
        "/saves/slot_3/save.json": "three",
    }
    assert out["namedDeleted"] == {
        "/saves/slot_1/save.json": "one",
        "/saves/slot_3/save.json": "three",
    }
    assert out["noList"] == {
        "/saves/slot_1/save.json": "one",
        "/saves/slot_2/save.json": "two",
        "/saves/slot_3/save.json": "three",
    }
    assert out["sentWins"]["/saves/slot_2/save.json"] == "two again"
    assert out["failedReturned"] is False
    assert out["failedAborted"] is True
    assert out["failed"] == {
        "/saves/slot_1/save.json": "one",
        "/saves/slot_2/save.json": "two",
        "/saves/slot_3/save.json": "three",
    }


@pytest.mark.parametrize(
    "asset,writer",
    [("boot.js", "function idbWrite("), ("console.js", "function keepFiles(")],
)
def test_no_sync_clears_the_store(asset, writer):
    page = readAsset(asset)
    body = page.split(writer, 1)[1]
    body = body[: body.index("\n    }\n")]
    assert ".clear(" not in body
    assert "mirrorToStore(tx.objectStore(IDB_STORE), files, deleted, log);" in body
    assert ".clear(" not in extract(asset, "function mirrorToStore(", "  ")
    # The abort handler is set before the writes, so a rolled-back sync is
    # reported rather than silent.
    assert body.index("tx.onabort") < body.index("mirrorToStore(")


FAKE_FS = r"""
// A tiny Emscripten-FS stand-in: a Map of file path -> text; directories are
// implied by the paths. unreadable: paths whose read throws.
function fakeFS(files, unreadable) {
  const isDir = (p) => [...files.keys()].some((f) => f.startsWith(p + "/"));
  return {
    files, unreadable: unreadable || new Set(),
    readdir(p) {
      if (!isDir(p)) throw new Error("ENOENT " + p);
      const names = new Set();
      for (const f of files.keys()) if (f.startsWith(p + "/")) names.add(f.slice(p.length + 1).split("/")[0]);
      return [".", "..", ...names];
    },
    stat(p) {
      if (files.has(p)) return { mode: 0o100644, mtime: 1, size: 1 };
      if (isDir(p)) return { mode: 0o040755, mtime: 1, size: 0 };
      throw new Error("ENOENT " + p);
    },
    readFile(p) {
      if (this.unreadable.has(p) || !files.has(p)) throw new Error("EIO " + p);
      return files.get(p);
    },
  };
}
const posted = [];
globalThis.self = { postMessage: (m) => posted.push(m) };
"""


@needsNode
def test_the_game_worker_names_only_files_this_session_saw_removed():
    worker = extract("game-worker.js", "function makeSyncSaves(", "") + "\n"
    worker += extract("game-worker.js", "function removedSince(", "")
    out = runNode(
        FAKE_FS
        + worker
        + r"""
const fs = new Map([["/saves/slot_1/save.json", "a"], ["/saves/slot_2/save.json", "b"]]);
const pyodide = { FS: fakeFS(fs) };
// Restored: both slots, plus a stray path outside the save directory and a
// slot 3 that (for whatever reason) never made it into the directory.
const restored = new Set(["/saves/slot_1/save.json", "/saves/slot_2/save.json",
                          "/saves/slot_3/save.json", "/elsewhere/x.json"]);
const sync = makeSyncSaves(pyodide, "/saves", restored, "[test]");
const steps = {};
// A walk that cannot read slot 2 sends nothing at all (all or nothing).
pyodide.FS.unreadable.add("/saves/slot_2/save.json");
sync(); steps.unreadablePosts = posted.splice(0).length;
pyodide.FS.unreadable.clear();
// A normal sync: slot 3 (restored, now absent) is named; the stray path
// outside /saves is not.
sync(); steps.first = posted.pop();
// The game deletes slot 2.
fs.delete("/saves/slot_2/save.json");
sync(); steps.afterDelete = posted.pop();
// Named once, not again.
sync(); steps.again = posted.pop();
// A new slot appears and is then removed: named, since this session sent it.
fs.set("/saves/slot_4/save.json", "d"); sync(); posted.pop();
fs.delete("/saves/slot_4/save.json"); sync(); steps.newThenGone = posted.pop();
console.log(JSON.stringify(steps));
"""
    )
    assert out["unreadablePosts"] == 0
    assert out["first"]["deleted"] == ["/saves/slot_3/save.json"]
    assert sorted(out["first"]["files"]) == [
        "/saves/slot_1/save.json",
        "/saves/slot_2/save.json",
    ]
    assert out["afterDelete"]["deleted"] == ["/saves/slot_2/save.json"]
    assert out["again"]["deleted"] == []
    assert out["newThenGone"]["deleted"] == ["/saves/slot_4/save.json"]


@needsNode
def test_a_save_the_worker_never_had_survives_the_next_save():
    # The reported shape end to end: the store holds slots 1 and 2, the
    # Worker's directory ends up with only slot 1, and the game saves. Slot 2
    # must still be stored afterwards.
    script = (
        FAKE_FS
        + FAKE_STORE
        + extract("game-worker.js", "function makeSyncSaves(", "")
        + "\n"
        + extract("game-worker.js", "function removedSince(", "")
        + "\n"
        + extract("boot.js", "function mirrorToStore(", "  ")
        + r"""
const store = fakeStore([["/saves/slot_1/save.json", "one"], ["/saves/slot_2/save.json", "two"]]);
const fs = new Map([["/saves/slot_1/save.json", "one, played on"]]);
const sync = makeSyncSaves({ FS: fakeFS(fs) }, "/saves", new Set(["/saves/slot_1/save.json"]), "[test]");
sync();
const m = posted.pop();
mirrorToStore(store, m.files, m.deleted, quiet);
console.log(JSON.stringify(asObject(store)));
"""
    )
    assert runNode(script) == {
        "/saves/slot_1/save.json": "one, played on",
        "/saves/slot_2/save.json": "two",
    }


@needsNode
def test_the_console_worker_names_only_kept_files_the_program_removed():
    worker = "const GAME_DIRECTORY = '/game';\n"
    worker += extract("console-worker.js", "function walkFiles(", "") + "\n"
    worker += extract("console-worker.js", "function makeFileSync(", "")
    out = runNode(
        FAKE_FS
        + worker
        + r"""
const fs = new Map([["/game/main.py", "code"], ["/game/save.txt", "s"], ["/game/scores.json", "1"]]);
const pyodide = { FS: fakeFS(fs) };
// main.py came with the bundle (mtime 1, untouched); save.txt was restored.
const pristine = new Map([["/game/main.py", 1]]);
const sync = makeFileSync(pyodide, pristine, new Set(["/game/save.txt"]), "[test]");
const steps = {};
sync(); steps.first = posted.pop();
pyodide.FS.unreadable.add("/game/scores.json");
fs.delete("/game/save.txt");
sync(); steps.unreadablePosts = posted.splice(0).length;
pyodide.FS.unreadable.clear();
sync(); steps.afterDelete = posted.pop();
console.log(JSON.stringify(steps));
"""
    )
    assert sorted(out["first"]["files"]) == ["/game/save.txt", "/game/scores.json"]
    assert out["first"]["deleted"] == []
    assert out["unreadablePosts"] == 0
    assert out["afterDelete"]["deleted"] == ["/game/save.txt"]
    # The bundle's own file is never kept and never named.
    assert "/game/main.py" not in json.dumps(out)
