"""The "<- Arcade" link back to the walkable hall (assets/hall.js) and how the
runtimes load it.

hall.js is run under Node (CI's runners have it) against a small stand-in for
the page: a window whose location and top can be set, and a document that
records what is put into it.
"""

import json
import shutil
import subprocess

import pytest

import tak.web
from tak.web import readAsset

NODE = shutil.which("node")
HALL = "https://play.danielstephenson.dev/hall/?at="


def test_hall_js_ships_with_the_package():
    assert "window.TakHall" in readAsset("hall.js")


@pytest.mark.parametrize(
    "asset, logLine",
    [
        ("boot.js", 'const log = config.logPrefix || "[tak]";'),
        ("console.js", 'const log = config.logPrefix || "[tak-console]";'),
    ],
)
def test_both_runtimes_load_the_link_first_thing(asset, logLine):
    source = readAsset(asset)
    call = 'loadHallLink(config.hallUrl || "/tak/hall.js");'
    assert call in source
    # Straight after the log prefix: before anything that can refuse to start
    # the game, so a browser that cannot run it still has its way back.
    assert source.index(call) == source.index(logLine) + len(logLine) + len("\n    ")
    loader = source[source.index("function loadHallLink(url)") :]
    loader = loader[: loader.index("\n  }\n")]
    # Framed pages and other hosts never even fetch hall.js.
    assert "window.top !== window.self" in loader
    assert "danielstephenson\\.dev$/.test(location.hostname)" in loader
    # Nothing here touches storage, the Worker or the page's input.
    for forbidden in ("indexedDB", "localStorage", "worker", "addEventListener", "open("):
        assert forbidden not in loader


HARNESS = r"""
function makeElement(tag) {
  return {
    tagName: tag.toUpperCase(), children: [], attributes: {}, listeners: {}, textContent: "", id: "",
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    insertBefore(child, before) {
      const i = before ? this.children.indexOf(before) : -1;
      if (i < 0) this.children.push(child); else this.children.splice(i, 0, child);
      child.parentNode = this; return child;
    },
    get firstChild() { return this.children[0] || null; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    querySelector(selector) {
      for (const c of this.children) {
        if (c.tagName === selector.toUpperCase()) return c;
        const found = c.querySelector && c.querySelector(selector);
        if (found) return found;
      }
      return null;
    },
  };
}
function makePage(hostname, framed) {
  const head = makeElement("head");
  const body = makeElement("body");
  body.appendChild(makeElement("h2"));
  const all = () => {
    const out = [];
    const walk = (n) => { out.push(n); (n.children || []).forEach(walk); };
    walk(head); walk(body); return out;
  };
  const doc = {
    head, body,
    createElement: makeElement,
    createTextNode: (text) => ({ nodeType: 3, textContent: text }),
    getElementById: (id) => all().find((n) => n.id === id) || null,
  };
  const win = { location: { hostname }, document: doc };
  win.self = win;
  win.top = framed ? { other: true } : win;
  return win;
}
function text(node) {
  return (node.children || []).map((c) => c.nodeType === 3 ? c.textContent : (c.textContent || text(c))).join("");
}

globalThis.window = globalThis;
require(process.argv[1]);
const H = globalThis.TakHall;
const out = { slugs: {}, links: {} };

for (const host of JSON.parse(process.argv[2])) out.slugs[host] = H.slugFor(host);
out.slugs["(undefined)"] = H.slugFor(undefined);

for (const [name, host, framed] of [["arcade", "tidewater.play.danielstephenson.dev", false],
                                    ["alias", "overwinter.danielstephenson.dev", false],
                                    ["framed", "tidewater.play.danielstephenson.dev", true],
                                    ["localhost", "localhost", false],
                                    ["portal", "danielstephenson.dev", false]]) {
  const win = makePage(host, framed);
  const link = H.attach(win);
  const again = H.attach(win);
  const first = win.document.body.firstChild;
  const record = { attached: !!link, sameOnSecondCall: link === again, bodyCount: win.document.body.children.length,
                   styles: win.document.head.children.length };
  if (link) {
    record.firstIsNav = first.tagName === "NAV" && first.id === "tak-hall-link";
    record.href = link.href;
    record.text = text(link);
    record.arrowHidden = link.children[0].attributes["aria-hidden"];
    record.ariaLabel = first.attributes["aria-label"];
    const stopped = {};
    for (const key of ["Enter", " ", "1", "Tab"]) {
      let s = false;
      link.listeners.keydown.forEach((fn) => fn({ key, stopPropagation() { s = true; } }));
      stopped[key] = s;
    }
    record.stopped = stopped;
  }
  out.links[name] = record;
}

// A frame whose top cannot even be compared counts as framed.
const hostile = makePage("tidewater.play.danielstephenson.dev", false);
Object.defineProperty(hostile, "top", { get() { throw new Error("SecurityError"); } });
out.links.hostileTop = { attached: !!H.attach(hostile), link: H.linkFor(hostile) };
console.log(JSON.stringify(out));
"""

HOSTS = {
    # arcade's own hosts: the first label is the slug.
    "tidewater.play.danielstephenson.dev": "tidewater",
    "night-ferry.play.danielstephenson.dev": "night-ferry",
    "guess-my-word.play.danielstephenson.dev": "guess-my-word",
    "cave.play.danielstephenson.dev": "cave",
    "Dice.Play.DanielStephenson.dev": "dice",
    "fishe.play.danielstephenson.dev.": "fishe",
    # the old hostnames arcade keeps as aliases of one game each.
    "tidewater.danielstephenson.dev": "tidewater",
    "overwinter.danielstephenson.dev": "overwinter",
    "fishe.danielstephenson.dev": "fishe",
    # not games on arcade.
    "api.play.danielstephenson.dev": None,
    "www.play.danielstephenson.dev": None,
    "play.danielstephenson.dev": None,
    "danielstephenson.dev": None,
    "www.danielstephenson.dev": None,
    "arcade.danielstephenson.dev": None,
    "night-ferry.danielstephenson.dev": None,
    "a.b.play.danielstephenson.dev": None,
    "tidewater.play.danielstephenson.dev.evil.example": None,
    "eviltidewater.danielstephenson.dev.example": None,
    "9lives.play.danielstephenson.dev": None,
    "localhost": None,
    "127.0.0.1": None,
    "": None,
}


@pytest.fixture(scope="module")
def runs():
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [NODE, "-e", HARNESS, tak.web.assetPath("hall.js"), json.dumps(list(HOSTS))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("host", list(HOSTS))
def test_the_slug_is_the_hostname_s_first_label_on_arcade_only(runs, host):
    assert runs["slugs"][host] == HOSTS[host]


def test_a_missing_hostname_has_no_slug(runs):
    assert runs["slugs"]["(undefined)"] is None


def test_a_top_level_game_page_links_to_its_own_cabinet(runs):
    link = runs["links"]["arcade"]
    assert link["attached"] and link["firstIsNav"]
    assert link["href"] == HALL + "tidewater"
    assert link["text"] == "← Arcade"
    assert link["arrowHidden"] == "true"
    assert link["ariaLabel"] == "Arcade"


def test_an_alias_host_links_to_the_game_it_aliases(runs):
    assert runs["links"]["alias"]["href"] == HALL + "overwinter"


def test_attaching_twice_draws_one_link(runs):
    link = runs["links"]["arcade"]
    assert link["sameOnSecondCall"]
    assert link["bodyCount"] == 2  # the link and the page's own title
    assert link["styles"] == 1


@pytest.mark.parametrize("place", ["framed", "localhost", "portal", "hostileTop"])
def test_no_link_framed_or_off_arcade(runs, place):
    link = runs["links"][place]
    assert link["attached"] is False
    if place != "hostileTop":
        assert link["bodyCount"] == 1 and link["styles"] == 0


def test_enter_on_the_link_follows_it_instead_of_reaching_the_game(runs):
    # The page's "Enter or Space continues" handler listens on document; the
    # focused link keeps those keys to itself, and nothing else.
    assert runs["links"]["arcade"]["stopped"] == {
        "Enter": True,
        " ": True,
        "1": False,
        "Tab": False,
    }
