// A small "← Arcade" link at the top of a tak game's page, back to the game's
// own cabinet in the walkable arcade hall:
//
//   https://<slug>.play.danielstephenson.dev  ->  https://play.danielstephenson.dev/hall/?at=<slug>
//
// boot.js and console.js load this (config.hallUrl, default /tak/hall.js) and
// call TakHall.attach(). The link is shown only when all of these hold:
//
//   - the page is a game on arcade, <slug>.play.danielstephenson.dev, or one
//     of the old hostnames that arcade keeps as an alias of exactly one slug
//     (LEGACY_ALIASES). Anywhere else - localhost, tak.web.serve, an unknown
//     host - there is no cabinet to go back to;
//   - the page is the top-level page. Framed (the hall's own overlay, or
//     danielstephenson.dev/play/<id>/play), whoever framed it has its own way
//     back.
//
// It is a plain link in the page's flow, above the title: a full page load
// (no window.open), nothing absolutely positioned, so it can cover neither the
// game's text nor its controls. It reads and writes no storage, sends no
// request, and leaves the game's input alone: the only key handling is on the
// link itself, so that Enter on the focused link follows the link instead of
// also reaching the page's "Enter continues" handler.

window.TakHall = (function () {
  "use strict";

  const HALL = "https://play.danielstephenson.dev/hall/?at=";
  const GAME_HOST = /^([a-z][a-z0-9-]{1,30})\.play\.danielstephenson\.dev$/;
  // Hosts under .play.danielstephenson.dev that are not games.
  const NOT_GAMES = { api: true, www: true };
  // Old hostnames arcade still serves as aliases (gateway config/arcade/games.yaml).
  // Only these: an alias that does not name exactly one game gets no link.
  const LEGACY_ALIASES = {
    "tidewater.danielstephenson.dev": "tidewater",
    "overwinter.danielstephenson.dev": "overwinter",
    "fishe.danielstephenson.dev": "fishe",
  };
  const ID = "tak-hall-link";

  // The game's slug for a hostname, or null when it is not a game on arcade.
  function slugFor(hostname) {
    if (typeof hostname !== "string") return null;
    const host = hostname.toLowerCase().replace(/\.$/, "");
    if (Object.prototype.hasOwnProperty.call(LEGACY_ALIASES, host)) return LEGACY_ALIASES[host];
    const match = GAME_HOST.exec(host);
    if (!match || NOT_GAMES[match[1]]) return null;
    return match[1];
  }

  function hrefFor(slug) {
    return HALL + encodeURIComponent(slug);
  }

  // True when this page is not inside a frame. A cross-origin parent can make
  // the comparison itself throw in odd embedders: treat that as framed.
  function topLevel(win) {
    try {
      return win.top === win.self;
    } catch (e) {
      return false;
    }
  }

  // The link's href for this page, or null when no link belongs here.
  function linkFor(win) {
    try {
      if (!topLevel(win)) return null;
      const slug = slugFor(win.location.hostname);
      return slug ? hrefFor(slug) : null;
    } catch (e) {
      return null;
    }
  }

  const STYLE =
    "#" + ID + "{display:block;margin:0 0 .4rem;font:13px/1.6 system-ui,-apple-system,sans-serif}" +
    "#" + ID + " a{color:#7fb0d0;text-decoration:none;border-radius:4px;padding:.15rem .3rem;margin-left:-.3rem}" +
    "#" + ID + " a:hover{text-decoration:underline}" +
    "#" + ID + " a:focus-visible{outline:2px solid #7fb0d0;outline-offset:1px}" +
    // A ~44px tap target on touch screens, without moving anything: the
    // padding is taken back by negative margins.
    "@media (pointer: coarse){#" + ID + " a{display:inline-block;padding:12px .5rem;margin:-12px 0 -12px -.5rem}}";

  // Put the link at the top of the page. Returns the link, or null when none
  // belongs on this page. Safe to call more than once.
  function attach(win) {
    win = win || window;
    try {
      const href = linkFor(win);
      if (!href) return null;
      const doc = win.document;
      const existing = doc.getElementById(ID);
      if (existing) return existing.querySelector("a");
      const style = doc.createElement("style");
      style.textContent = STYLE;
      doc.head.appendChild(style);
      const nav = doc.createElement("nav");
      nav.id = ID;
      nav.setAttribute("aria-label", "Arcade");
      const link = doc.createElement("a");
      link.href = href;
      link.title = "Back to this game's cabinet in the arcade hall";
      const arrow = doc.createElement("span");
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "← ";
      link.appendChild(arrow);
      link.appendChild(doc.createTextNode("Arcade"));
      link.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") e.stopPropagation();
      });
      nav.appendChild(link);
      doc.body.insertBefore(nav, doc.body.firstChild);
      return link;
    } catch (e) {
      return null;
    }
  }

  return { attach: attach, slugFor: slugFor, linkFor: linkFor, hrefFor: hrefFor };
})();
