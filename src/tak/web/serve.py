# @author Daniel McCoy Stephenson
"""Static file server for a game's browser-native (Pyodide) build.

This server does not run the game - it only hands the browser the files it
needs, and the game then runs in the player's own tab with its saves in that
browser's IndexedDB. Every visitor gets their own game and their own save
slots, and the server keeps no state at all.

A game exposes it with a few lines (Tidewater's web/serve.py, say)::

    from tak.web.serve import main
    main(root=REPOSITORY_ROOT, title="Tidewater", envPrefix="TIDEWATER")

Routes:
  /  /play  /play/  /index.html  -> <root>/web/index.html
  /web/...                       -> <root>/web/ (game.zip, the entry point)
  /tak/...                       -> the kit's assets (client, boot, worker)
  everything else                -> 404

The Cross-Origin-Opener-Policy / Cross-Origin-Embedder-Policy headers below are
not optional: without them the page is not cross-origin isolated, and
SharedArrayBuffer - which is how the player's input reaches the blocked game
Worker - is not available at all. boot.js says so on screen if they are
missing, which is the usual symptom of a proxy in front of this server dropping
them.
"""

import errno
import http.server
import os
import posixpath
from urllib.parse import unquote, urlparse

from tak.web import ASSET_DIRECTORY, ASSET_URL_PREFIX

INDEX_PATHS = ("/", "/play", "/play/", "/index.html")

# <PREFIX>_WEB_PORT is read here and by the server-backed front-end, with a
# different default in each: 8080 for this server, 8000 for the game running
# behind WebUserInterface. They are separate programs that can be run at the
# same time, so the defaults are deliberately not shared - the check on the
# value is, so a misspelled port names itself whichever one the player started.
DEFAULT_PORT = "8080"
DEFAULT_HOST = "127.0.0.1"


def _makeHandler(root, title):
    webDirectory = os.path.join(root, "web")

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in INDEX_PATHS:
                self._sendIndex()
                return
            if path.startswith(ASSET_URL_PREFIX):
                self._sendAsset(path[len(ASSET_URL_PREFIX) :])
                return
            if not path.startswith("/web/"):
                self.send_error(404, "Not found")
                return
            super().do_GET()

        def _sendIndex(self):
            indexPath = os.path.join(webDirectory, "index.html")
            try:
                with open(indexPath, "rb") as indexFile:
                    body = indexFile.read()
            except OSError as e:
                self.send_error(
                    500,
                    "%s's page is missing" % title,
                    "Could not read %s: %s. That file ships in the game's web/ "
                    "directory - serve the game from a complete checkout."
                    % (indexPath, e),
                )
                return
            self._sendBytes("text/html; charset=utf-8", body)

        def _sendAsset(self, name):
            # Only the flat asset directory is served: a name with a path in it
            # is not one of ours, whatever it resolves to.
            if not name or posixpath.basename(name) != name:
                self.send_error(404, "Not found")
                return
            assetPath = os.path.join(ASSET_DIRECTORY, name)
            try:
                with open(assetPath, "rb") as assetFile:
                    body = assetFile.read()
            except OSError:
                self.send_error(404, "Not found")
                return
            self._sendBytes(self.guess_type(assetPath), body)

        def _sendBytes(self, contentType, body):
            self.send_response(200)
            self.send_header("Content-Type", contentType)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def end_headers(self):
            # Required for SharedArrayBuffer, which the Pyodide front-end uses
            # to deliver input to the (blocked) game Worker.
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            super().end_headers()

        def log_message(self, *args):
            pass  # keep the container's logs to what the game itself says

    return _Handler


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _variables(envPrefix):
    return "%s_WEB_HOST" % envPrefix, "%s_WEB_PORT" % envPrefix


def resolvePort(envPrefix):
    """The port to serve on, or a ValueError naming the variable that is wrong."""
    _, portVariable = _variables(envPrefix)
    portText = os.environ.get(portVariable, DEFAULT_PORT)
    try:
        return int(portText)
    except ValueError:
        raise ValueError("%s must be an integer, got: %r" % (portVariable, portText))


def bindServer(root, title, envPrefix, host, port):
    """Bind the server, turning a refused address into a sentence.

    A port that is already listening - a second copy of the game, most often -
    otherwise surfaces as an errno raised from inside http.server, naming
    neither the game nor the variable the player would have to change."""
    hostVariable, portVariable = _variables(envPrefix)
    try:
        return _Server((host, port), _makeHandler(root, title))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            reason = "something else is already listening there"
        elif e.errno == errno.EACCES:
            reason = "this process is not allowed to use that port"
        else:
            reason = str(e)
        raise OSError(
            "%s could not be served at http://%s:%s/: %s. Set %s to a free port "
            "(or %s to an address this machine can bind) and start it again."
            % (title, host, port, reason, portVariable, hostVariable)
        ) from e


def main(root, title="tak", envPrefix="TAK"):
    hostVariable, _ = _variables(envPrefix)
    host = os.environ.get(hostVariable, DEFAULT_HOST)
    port = resolvePort(envPrefix)
    # Bound before the URL is announced, so a failure is never preceded by an
    # address that was never served.
    server = bindServer(root, title, envPrefix, host, port)
    print("%s is being served at http://%s:%s/" % (title, host, port))
    print("Open that URL to play. Press Ctrl+C here to stop.")
    server.serve_forever()
