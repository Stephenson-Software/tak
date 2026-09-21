import io
import json
import os
import socket
import threading
import urllib.error
import urllib.request
import zipfile

import pytest

import tak
from tak.web import ASSET_DIRECTORY, readAsset
from tak.web import serve as serveModule
from tak.web.bundle import build


REQUIRED_ASSETS = ("client.js", "client.css", "boot.js", "game-worker.js")


def test_the_kit_ships_its_browser_assets():
    for name in REQUIRED_ASSETS:
        assert os.path.exists(os.path.join(ASSET_DIRECTORY, name)), name
    assert "window.TakClient" in readAsset("client.js")
    assert "window.TakBoot" in readAsset("boot.js")
    assert "SharedArrayBuffer" in readAsset("boot.js")


def test_client_renders_header_chips_generically():
    client = readAsset("client.js")
    assert "chip.class" in client and "h.chips" in client
    assert "money" not in client and "fish" not in client.lower()


def test_worker_takes_its_configuration_from_the_init_message():
    worker = readAsset("game-worker.js")
    for key in ("bundleUrl", "entry", "saveDir", "saveDirEnv", "idbName", "packages"):
        assert "config.%s" % key in worker, key
    assert "'jsonschema'" in worker


@pytest.fixture
def gameRoot(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text(
        "<html><title>Tidewater</title></html>"
    )
    (tmp_path / "web" / "game.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (tmp_path / "secret.txt").write_text("not served")
    return str(tmp_path)


@pytest.fixture
def server(gameRoot):
    httpd = serveModule.bindServer(gameRoot, "Tidewater", "TIDEWATER", "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(base, path):
    return urllib.request.urlopen(base + path, timeout=2)


@pytest.mark.parametrize("path", ["/", "/play", "/play/", "/index.html"])
def test_index_paths_serve_the_games_page_with_isolation_headers(server, path):
    response = get(server, path)
    assert b"Tidewater" in response.read()
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Embedder-Policy"] == "require-corp"


def test_game_web_directory_is_served(server):
    assert get(server, "/web/game.zip").read().startswith(b"PK")


def test_kit_assets_are_served_under_tak(server):
    response = get(server, "/tak/client.js")
    assert b"window.TakClient" in response.read()
    assert "javascript" in response.headers["Content-Type"]
    assert response.headers["Cross-Origin-Embedder-Policy"] == "require-corp"


@pytest.mark.parametrize(
    "path", ["/secret.txt", "/tak/", "/tak/../serve.py", "/tak/nope.js", "/other"]
)
def test_everything_else_is_404(server, path):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(server, path)
    assert error.value.code == 404


def test_missing_index_is_a_500_that_names_the_file(gameRoot):
    os.remove(os.path.join(gameRoot, "web", "index.html"))
    httpd = serveModule.bindServer(gameRoot, "Tidewater", "TIDEWATER", "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            get("http://127.0.0.1:%d" % httpd.server_address[1], "/")
        assert error.value.code == 500
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_port_comes_from_the_prefixed_variable(monkeypatch):
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "9090")
    assert serveModule.resolvePort("TIDEWATER") == 9090
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "lots")
    with pytest.raises(ValueError) as error:
        serveModule.resolvePort("TIDEWATER")
    assert "TIDEWATER_WEB_PORT" in str(error.value)


def test_taken_port_is_explained(gameRoot):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        with pytest.raises(OSError) as error:
            serveModule.bindServer(
                gameRoot,
                "Tidewater",
                "TIDEWATER",
                "127.0.0.1",
                blocker.getsockname()[1],
            )
    finally:
        blocker.close()
    assert "Tidewater" in str(error.value) and "TIDEWATER_WEB_PORT" in str(error.value)


def test_bundle_carries_the_game_and_the_kit(tmp_path):
    (tmp_path / "src" / "game").mkdir(parents=True)
    (tmp_path / "src" / "game" / "__init__.py").write_text("")
    (tmp_path / "src" / "game" / "__pycache__").mkdir()
    (tmp_path / "src" / "game" / "__pycache__" / "x.pyc").write_bytes(b"")
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "save.json").write_text("{}")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "pyodide_main.py").write_text("print('hi')")
    (tmp_path / "version.txt").write_text("0.1.0")

    output = build(
        str(tmp_path), extraFiles=("version.txt", "web/pyodide_main.py", "missing.txt")
    )

    with zipfile.ZipFile(output) as bundle:
        names = set(bundle.namelist())
    assert "src/game/__init__.py" in names
    assert "schemas/save.json" in names
    assert "web/pyodide_main.py" in names and "version.txt" in names
    assert "src/tak/__init__.py" in names
    assert "src/tak/ui/pyodide.py" in names
    assert "src/tak/web/assets/client.js" in names
    assert not any(n.endswith(".pyc") for n in names)
    assert "missing.txt" not in names


def test_bundle_can_leave_the_kit_out(tmp_path):
    (tmp_path / "src").mkdir()
    output = build(str(tmp_path), includeTak=False)
    with zipfile.ZipFile(output) as bundle:
        assert not any(n.startswith("src/tak/") for n in bundle.namelist())
