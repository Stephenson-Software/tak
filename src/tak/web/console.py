# @author Daniel McCoy Stephenson
"""The page for tak's console runtime: an ordinary input()/print() program,
unmodified, running in the browser under Pyodide.

Where tak's own front-ends (tak.ui) need a game written against the kit's UI
contract, this runs any console program as it is: its prompts and output go to
a terminal on the page, the player's lines come back through a blocking stdin
(console-worker.js), and any file the program writes is kept in the browser's
IndexedDB. It is meant for small console games that predate the kit and would
gain nothing from a rewrite.

A game builds its bundle with tak.web.bundle as usual (its own files plus this
package), and writes its page with::

    from tak.web.console import page
    with open("web/index.html", "w") as out:
        out.write(page(title="Guess My Word", entry="src/guessMyWord.py",
                       idbName="guess-my-word-files"))

The page loads /tak/console.css, /tak/console.js and /tak/console-worker.js,
which tak.web.serve and arcade both serve; like every tak browser build it must
be served cross-origin isolated (COOP/COEP), because stdin is shared memory.
"""

import json

DEFAULT_BUNDLE_URL = "/web/game.zip"


def _escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def page(title, entry, idbName, tagline="", bundleUrl=DEFAULT_BUNDLE_URL, packages=(), footer=""):
    """The index.html for a console game.

    entry is the script to run as __main__, as a path inside the bundle (the
    same path it has in the repository). idbName names the IndexedDB database
    the program's files are kept in - unique per game, and never changed once
    players have saves in it. footer is optional, already-safe HTML."""
    if not entry or entry.startswith("/") or ".." in entry.split("/"):
        raise ValueError("entry must be a relative path inside the bundle, got %r" % entry)
    if not idbName:
        raise ValueError("idbName is required: it is where the game's files are kept")
    config = {
        "bundleUrl": bundleUrl,
        "entry": entry,
        "idbName": idbName,
        "packages": list(packages),
        "elementId": "console",
        "logPrefix": "[%s]" % title,
    }
    # </script> cannot appear inside the inline script, whatever the title says.
    configJson = json.dumps(config, indent=2, sort_keys=True).replace("</", "<\\/")
    taglineHtml = ' <span class="tagline">— %s</span>' % _escape(tagline) if tagline else ""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<link rel="stylesheet" href="/tak/console.css">
<style>
  body { background: #0b0f14; color: #d6e2ee; font-family: system-ui, sans-serif; margin: 0; padding: 1rem; }
  h1 { font-size: 1.3rem; margin: 0 0 0.75rem; }
  .tagline { color: #7d8b99; font-weight: normal; }
  footer { color: #7d8b99; font-size: 0.85rem; margin-top: 1rem; text-align: center; }
  footer a { color: #7fb0d0; }
</style>
</head>
<body>
<h1>%(title)s%(tagline)s</h1>
<div id="console"></div>
<footer>%(footer)s</footer>
<script src="/tak/console.js"></script>
<script>
TakConsole.start(%(config)s);
</script>
</body>
</html>
""" % {
        "title": _escape(title),
        "tagline": taglineHtml,
        "footer": footer,
        "config": configJson,
    }
