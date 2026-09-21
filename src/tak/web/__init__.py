# @author Daniel McCoy Stephenson
"""The browser side of the kit.

assets/   client.js + client.css (the renderer both web front-ends share),
          boot.js + game-worker.js (the Pyodide transport and runtime loader)
page.py   the single page the server-backed front-end (tak.ui.web) serves,
          with the kit's client and stylesheet inlined
serve.py  the static server for a Pyodide build - sends the COOP/COEP headers
          SharedArrayBuffer needs, and serves the kit's assets at /tak/
bundle.py builds the game.zip the Worker downloads, with this package inside
"""

import os

ASSET_DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# The URL prefix the kit's assets are served under, by tak.web.serve and by
# whatever a deployment puts in front of it. A game's index.html references
# them here, so the prefix is fixed rather than configurable.
ASSET_URL_PREFIX = "/tak/"


def assetPath(name):
    return os.path.join(ASSET_DIRECTORY, name)


def readAsset(name):
    """Read one of the kit's browser assets, or explain why it could not."""
    path = assetPath(name)
    try:
        with open(path, "r", encoding="utf-8") as assetFile:
            return assetFile.read()
    except OSError as e:
        raise RuntimeError(
            "tak could not read its browser asset %r (looked in %s): %s. The "
            "file ships inside the tak package under tak/web/assets/; an "
            "install that lacks it is incomplete - reinstall the package, or "
            "if this is a Pyodide bundle, rebuild it with tak.web.bundle."
            % (name, path, e)
        ) from e
