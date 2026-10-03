"""The page side of tak.arcade (assets/arcade.js) and how the tak runtime wires it in.

arcade.js is run under Node (CI's runners have it) with a stubbed fetch and a
stubbed page location; no request leaves the machine.
"""

import json
import shutil
import subprocess

import pytest

import tak.web
from tak.web import readAsset

NODE = shutil.which("node")


def test_arcade_js_ships_with_the_package():
    assert "window.TakArcade" in readAsset("arcade.js")


def test_the_worker_posts_reports_to_the_page():
    worker = readAsset("game-worker.js")
    assert "globalThis.takArcade" in worker
    assert "type: 'arcade', request" in worker
    # Installed before the game's Python runs.
    assert worker.index("globalThis.takArcade") < worker.index("pyodide.runPythonAsync")


def test_boot_loads_arcade_js_only_when_a_game_reports():
    boot = readAsset("boot.js")
    assert 'if (message.type === "arcade") { arcade(message.request); return; }' in boot
    assert 'config.arcadeUrl || "/tak/arcade.js"' in boot
    # Not loaded up front like saves.js: only inside the report handler.
    assert boot.index('"/tak/arcade.js"') > boot.index("function arcade(request)")


HARNESS = r"""
globalThis.window = globalThis;
const calls = [];
let session = { signedIn: true };
let answers = [];       // statuses for the POSTs, in order; "fail" = network error
globalThis.fetch = async (url, init) => {
  calls.push({ url, method: (init && init.method) || "GET", credentials: init && init.credentials,
               headers: (init && init.headers) || {}, body: init && init.body });
  if (url.endsWith("/v1/session")) {
    if (session === "fail") throw new TypeError("Failed to fetch");
    return { ok: true, status: 200, json: async () => session };
  }
  const next = answers.length ? answers.shift() : 200;
  if (next === "fail") throw new TypeError("Failed to fetch");
  return { ok: next < 400, status: next, json: async () => ({}) };
};
require(process.argv[1]);
const A = globalThis.TakArcade;
A._setRetryDelay(1);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const at = (url) => ({ protocol: url.split("//")[0], hostname: url.split("//")[1] });

(async () => {
  const out = {};
  const run = async (name, setup, reports) => {
    calls.length = 0;
    setup();
    const results = [];
    for (const r of reports) results.push(await A.handle(r));
    await wait(30);
    out[name] = { results, calls: calls.map((c) => ({ ...c })) };
  };
  const score = JSON.stringify({ kind: "score", board: "most-money", value: 12450, run: "r1" });
  const unlock = JSON.stringify({ kind: "unlock", achievement: "first-catch" });

  await run("signedIn", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); session = { signedIn: true }; answers = []; }, [score, unlock]);
  await run("signedOut", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); session = { signedIn: false }; }, [score, unlock]);
  for (const [name, url] of [["localhost", "http://localhost:8000"], ["alias", "https://fishe.danielstephenson.dev"],
                             ["http", "http://fishe.play.danielstephenson.dev"], ["service", "https://api.play.danielstephenson.dev"],
                             ["portal", "https://danielstephenson.dev"], ["lookalike", "https://fishe.play.danielstephenson.dev.evil.example"]]) {
    await run("off-" + name, () => { A._setPlace(() => at(url)); session = { signedIn: true }; }, [score]);
  }
  await run("malformed", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); session = { signedIn: true }; },
    ["{not json", "null", JSON.stringify({ kind: "score", board: "../admin", value: 1 }),
     JSON.stringify({ kind: "score", board: "b-1", value: "1" }), JSON.stringify({ kind: "score", board: "b-1", value: 1, run: "a b" }),
     JSON.stringify({ kind: "unlock", achievement: "X" }), JSON.stringify({ kind: "delete" })]);
  await run("serverErrorRetriedOnce", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); answers = [503, 503, 503]; }, [unlock]);
  await run("networkRetriedOnce", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); answers = ["fail", 200]; }, [unlock]);
  await run("refusalNotRetried", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); answers = [422]; }, [score]);
  await run("sessionUnknown", () => { A._setPlace(() => at("https://fishe.play.danielstephenson.dev")); session = "fail"; answers = []; }, [unlock]);
  await run("placeThrows", () => { A._setPlace(() => { throw new Error("no location"); }); }, [unlock]);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def runs():
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [NODE, "-e", HARNESS, tak.web.assetPath("arcade.js")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def posts(run):
    return [c for c in run["calls"] if c["method"] == "POST"]


def test_a_signed_in_player_s_reports_are_credentialed_json_writes(runs):
    run = runs["signedIn"]
    assert run["results"] == [200, 200]
    sessionChecks = [c for c in run["calls"] if c["url"].endswith("/v1/session")]
    assert len(sessionChecks) == 1 and sessionChecks[0]["credentials"] == "include"
    score, unlock = posts(run)
    assert score["url"] == "https://api.play.danielstephenson.dev/v1/scores/most-money"
    assert json.loads(score["body"]) == {"value": 12450, "run": "r1"}
    assert (
        unlock["url"]
        == "https://api.play.danielstephenson.dev/v1/achievements/first-catch"
    )
    assert json.loads(unlock["body"]) == {}
    for call in (score, unlock):
        assert call["credentials"] == "include"
        assert call["headers"] == {
            "Content-Type": "application/json",
            "X-Play-Client": "1",
        }


def test_nothing_is_sent_for_a_signed_out_player(runs):
    run = runs["signedOut"]
    assert run["results"] == [None, None]
    assert posts(run) == []


@pytest.mark.parametrize(
    "place", ["localhost", "alias", "http", "service", "portal", "lookalike"]
)
def test_nothing_is_sent_off_arcade(runs, place):
    run = runs["off-" + place]
    assert run["results"] == [None]
    assert run["calls"] == []


def test_malformed_reports_are_dropped(runs):
    run = runs["malformed"]
    assert all(result is None for result in run["results"])
    assert run["calls"] == []


def test_a_server_error_is_retried_once(runs):
    assert len(posts(runs["serverErrorRetriedOnce"])) == 2


def test_a_network_failure_is_retried_once(runs):
    assert len(posts(runs["networkRetriedOnce"])) == 2


def test_a_refusal_is_final(runs):
    run = runs["refusalNotRetried"]
    assert run["results"] == [422]
    assert len(posts(run)) == 1


def test_an_unknown_session_still_tries_the_report(runs):
    assert len(posts(runs["sessionUnknown"])) == 1


def test_a_page_without_a_location_sends_nothing(runs):
    run = runs["placeThrows"]
    assert run["results"] == [None]
    assert run["calls"] == []
