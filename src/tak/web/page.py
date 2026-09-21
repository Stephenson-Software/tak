# @author Daniel McCoy Stephenson
"""The single-page client of the server-backed front-end (tak.ui.web).

Kept apart from the front-end class so a contributor can find the class
without scrolling past the page's JavaScript-in-a-string. The browser client
proper (client.js, client.css) lives in assets/ and is inlined here.
"""

from tak.web import readAsset

# Read on first use rather than at import, and cached after. This module is
# imported by both web front-ends (tak.ui.web and tak.ui.pyodide), and under
# Pyodide the page is never built: the browser fetches the assets over HTTP,
# so only the server-backed front-end has any reason to read them.
_clientAssetCache = {}


def _clientAsset(name):
    if name not in _clientAssetCache:
        _clientAssetCache[name] = readAsset(name)
    return _clientAssetCache[name]


DEFAULT_TIP = (
    "Tip: click an option or press its number key (1-9). Enter or Space continues."
)


def htmlPage(title, tagline="", tip=DEFAULT_TIP):
    """The single-page client for the server-backed front-end.

    It polls /state and renders whatever screen the game is currently waiting
    on, posting the player's response to /input. The kit's renderer and styles
    are inlined so the server stays a two-route affair and the page needs a
    single request."""
    taglineHtml = (
        ' <span class="tagline">— %s</span>' % _escape(tagline) if tagline else ""
    )
    return (
        """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>"""
        + _escape(title)
        + """</title>
<style>
"""
        + _clientAsset("client.css")
        + """</style>
</head>
<body>
<h2>"""
        + _escape(title)
        + taglineHtml
        + """</h2>
<div id="app">Connecting&hellip;</div>
<p class="controls">"""
        + _escape(tip)
        + """</p>
<script>
"""
        + _clientAsset("client.js")
        + """
// Transport: this front-end runs the game on a server, so responses are POSTed
// and new screens are discovered by polling. (The Pyodide front-end swaps this
// block for a Worker/SharedArrayBuffer transport - see boot.js - and shares
// everything above.)
TakClient.init(function (value) {
  fetch("/input", { method: "POST", body: JSON.stringify({ value: value }) });
});
let version = -1;
let failures = 0;
async function poll() {
  try {
    const response = await fetch("/state");
    const state = await response.json();
    const recovered = failures >= 5;
    failures = 0;
    if (recovered) version = -1;  // force a re-render to clear the disconnect banner
    if (state.version !== version) { version = state.version; TakClient.render(state.screen); }
    // The game shuts its server down once this screen has gone out, so there
    // is nothing left to poll for: stop, rather than spend the rest of the
    // tab's life failing to reach a process that has finished.
    if (state.screen && state.screen.type === "ended") { return; }
  } catch (e) {
    failures++;
    // Don't clobber the intentional "game ended" screen with a scary banner.
    const screen = TakClient.getCurrentScreen();
    if (failures === 5 && !(screen && screen.type === "ended")) {
      TakClient.renderDisconnected();
    }
  }
  setTimeout(poll, 300);
}
poll();
</script>
</body>
</html>
"""
    )


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
