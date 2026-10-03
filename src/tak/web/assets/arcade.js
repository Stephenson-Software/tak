// The page side of tak.arcade (Stephenson-Software RFC 0014 §2): sends a tak
// game's scores and achievement unlocks to arcade-social from the main thread.
//
// boot.js loads this on the game's first report and hands it each request the
// Worker posted ({ type: 'arcade', request: '<json>' }), as
//
//   TakArcade.handle('{"kind": "score", "board": "most-money", "value": 12450}')
//   TakArcade.handle('{"kind": "unlock", "achievement": "first-catch"}')
//
// Everything is fire-and-forget: handle() returns at once, never throws, and
// the game never learns how a report went. A report is dropped, silently:
//
//   - unless the page is a game on arcade, https://<slug>.play.danielstephenson.dev
//     (the service decides the game from the request's Origin, so a page
//     anywhere else - localhost, an alias, a desktop build - has nothing to
//     report to);
//   - when the player is not signed in (asked once a minute at /v1/session;
//     a score earned signed out is never uploaded later, RFC 0014 OQ6);
//   - when the service refuses it (an undeclared board, a value out of
//     bounds, no display name yet, a rate limit);
//   - when the service cannot be reached after one retry (RFC 0014 §2:
//     "queued in memory for the session and retried once").

window.TakArcade = (function () {
  "use strict";

  const API = "https://api.play.danielstephenson.dev";
  const GAME_HOST = /^[a-z][a-z0-9-]{1,30}\.play\.danielstephenson\.dev$/;
  const ID = /^[a-z][a-z0-9-]{1,30}$/;
  const RUN = /^[A-Za-z0-9._:-]{1,64}$/;
  const SESSION_TTL_MS = 60 * 1000;
  let retryMs = 30 * 1000;

  let session = null;   // { at, signedIn: Promise<boolean> }

  // The page's own location; replaceable in tests.
  let place = function () { return window.location; };

  function onArcade() {
    try {
      const where = place();
      return where.protocol === "https:" && GAME_HOST.test(where.hostname) &&
        where.hostname.indexOf("api.") !== 0;
    } catch (e) {
      return false;
    }
  }

  function signedIn() {
    if (session && Date.now() - session.at < SESSION_TTL_MS) return session.signedIn;
    const answer = fetch(API + "/v1/session", { credentials: "include" })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (body) { return !!(body && body.signedIn === true); })
      .catch(function () { return null; });   // unknown: try the report anyway
    session = { at: Date.now(), signedIn: answer };
    return answer;
  }

  // The request as the service wants it, or null when it is not one.
  function toRequest(text) {
    let message;
    try { message = JSON.parse(text); } catch (e) { return null; }
    if (!message || typeof message !== "object") return null;
    if (message.kind === "score") {
      if (!ID.test(message.board) || typeof message.value !== "number" || !isFinite(message.value)) return null;
      const body = { value: message.value };
      if (message.run !== undefined) {
        if (typeof message.run !== "string" || !RUN.test(message.run)) return null;
        body.run = message.run;
      }
      return { path: "/v1/scores/" + message.board, body: body };
    }
    if (message.kind === "unlock" && ID.test(message.achievement)) {
      return { path: "/v1/achievements/" + message.achievement, body: {} };
    }
    return null;
  }

  function send(request, retried) {
    return fetch(API + request.path, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-Play-Client": "1" },
      body: JSON.stringify(request.body),
    }).then(function (response) {
      // A refusal (4xx) is final; a server error is worth one more try.
      if (response.status >= 500 && !retried) retry(request);
      return response.status;
    }, function () {
      if (!retried) retry(request);
      return 0;
    });
  }

  function retry(request) {
    setTimeout(function () { send(request, true); }, retryMs);
  }

  // Send one report from the Worker. Returns a promise for tests; callers
  // ignore it. Resolves to the HTTP status, 0 for a network failure, or null
  // when nothing was sent.
  function handle(text) {
    try {
      const request = toRequest(text);
      if (!request || !onArcade() || typeof fetch !== "function") return Promise.resolve(null);
      return signedIn().then(function (known) {
        if (known === false) return null;
        return send(request, false);
      }).catch(function () { return null; });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  return {
    handle: handle,
    // For tests only.
    _setPlace: function (fn) { place = fn; session = null; },
    _setRetryDelay: function (ms) { retryMs = ms; },
  };
})();
