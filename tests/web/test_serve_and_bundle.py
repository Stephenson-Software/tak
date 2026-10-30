import errno
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


REQUIRED_ASSETS = (
    "client.js",
    "client.css",
    "boot.js",
    "game-worker.js",
    "console.js",
    "console.css",
    "console-worker.js",
    "saves.js",
    "arcade.js",
    "cloud.js",
    "hall.js",
)


def test_the_kit_ships_its_browser_assets():
    for name in REQUIRED_ASSETS:
        assert os.path.exists(os.path.join(ASSET_DIRECTORY, name)), name
    assert "window.TakClient" in readAsset("client.js")
    assert "window.TakBoot" in readAsset("boot.js")
    assert "SharedArrayBuffer" in readAsset("boot.js")


def test_every_asset_in_the_directory_is_a_required_one():
    # A new asset has to be added above, which is also the reminder that it
    # must match pyproject.toml's package-data glob to reach an install.
    assert sorted(os.listdir(ASSET_DIRECTORY)) == sorted(REQUIRED_ASSETS)


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


@pytest.mark.parametrize("name", REQUIRED_ASSETS)
def test_every_kit_asset_is_served_under_tak(server, name):
    response = get(server, "/tak/" + name)
    assert response.read() == readAsset(name).encode("utf-8")
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
        assert os.path.join("web", "index.html") in error.value.read().decode()
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


def refuseToBind(monkeypatch, errnoValue, message):
    def _refuse(*args, **kwargs):
        raise OSError(errnoValue, message)

    monkeypatch.setattr(serveModule, "_Server", _refuse)


def test_a_forbidden_port_is_explained_and_names_both_variables(gameRoot, monkeypatch):
    refuseToBind(monkeypatch, errno.EACCES, "Permission denied")
    with pytest.raises(OSError) as error:
        serveModule.bindServer(gameRoot, "Tidewater", "TIDEWATER", "0.0.0.0", 80)
    message = str(error.value)
    assert "http://0.0.0.0:80/" in message
    assert "not allowed to use that port" in message
    assert "TIDEWATER_WEB_PORT" in message and "TIDEWATER_WEB_HOST" in message
    assert error.value.__cause__.errno == errno.EACCES


def test_any_other_bind_failure_keeps_the_systems_reason(gameRoot, monkeypatch):
    refuseToBind(monkeypatch, errno.EADDRNOTAVAIL, "Cannot assign requested address")
    with pytest.raises(OSError) as error:
        serveModule.bindServer(gameRoot, "Tidewater", "TIDEWATER", "10.9.9.9", 8080)
    message = str(error.value)
    assert "Cannot assign requested address" in message
    assert "TIDEWATER_WEB_HOST" in message


class FakeServer:
    def __init__(self):
        self.served = False

    def serve_forever(self):
        self.served = True


def recordBinds(monkeypatch):
    fake = FakeServer()
    binds = []

    def _bind(root, title, envPrefix, host, port):
        binds.append((root, title, envPrefix, host, port))
        return fake

    monkeypatch.setattr(serveModule, "bindServer", _bind)
    return fake, binds


def test_main_serves_on_the_documented_default_address(monkeypatch, capsys):
    monkeypatch.delenv("TIDEWATER_WEB_HOST", raising=False)
    monkeypatch.delenv("TIDEWATER_WEB_PORT", raising=False)
    fake, binds = recordBinds(monkeypatch)
    serveModule.main("/game", title="Tidewater", envPrefix="TIDEWATER")
    assert binds == [("/game", "Tidewater", "TIDEWATER", "127.0.0.1", 8080)]
    assert fake.served
    out = capsys.readouterr().out
    assert "Tidewater is being served at http://127.0.0.1:8080/" in out


def test_main_takes_host_and_port_from_the_prefixed_variables(monkeypatch, capsys):
    monkeypatch.setenv("TIDEWATER_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "9090")
    monkeypatch.setenv("TAK_WEB_PORT", "1")
    fake, binds = recordBinds(monkeypatch)
    serveModule.main("/game", title="Tidewater", envPrefix="TIDEWATER")
    assert binds[0][3:] == ("0.0.0.0", 9090)
    assert "http://0.0.0.0:9090/" in capsys.readouterr().out


def test_main_announces_nothing_when_the_address_cannot_be_bound(monkeypatch, capsys):
    monkeypatch.delenv("TIDEWATER_WEB_PORT", raising=False)

    def _refuse(*args):
        raise OSError("Tidewater could not be served")

    monkeypatch.setattr(serveModule, "bindServer", _refuse)
    with pytest.raises(OSError):
        serveModule.main("/game", title="Tidewater", envPrefix="TIDEWATER")
    assert capsys.readouterr().out == ""


def test_main_rejects_a_misspelled_port_before_binding(monkeypatch, capsys):
    monkeypatch.setenv("TIDEWATER_WEB_PORT", "eighty")
    fake, binds = recordBinds(monkeypatch)
    with pytest.raises(ValueError) as error:
        serveModule.main("/game", title="Tidewater", envPrefix="TIDEWATER")
    assert "TIDEWATER_WEB_PORT" in str(error.value)
    assert binds == [] and capsys.readouterr().out == ""


def test_bundle_carries_the_game_and_the_kit(tmp_path, capsys):
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
    for asset in REQUIRED_ASSETS:
        assert "src/tak/web/assets/" + asset in names, asset
    assert not any(n.endswith(".pyc") for n in names)
    assert "missing.txt" not in names
    err = capsys.readouterr().err
    assert "missing.txt" in err and str(tmp_path) in err
    assert "version.txt" not in err and "pyodide_main.py" not in err


def test_bundle_can_leave_the_kit_out(tmp_path):
    (tmp_path / "src").mkdir()
    output = build(str(tmp_path), includeTak=False)
    with zipfile.ZipFile(output) as bundle:
        assert not any(n.startswith("src/tak/") for n in bundle.namelist())


def test_a_failed_save_restore_never_syncs():
    # boot.js replaces the whole store with each save, so syncing after a
    # failed read would replace the player's saves with this session's.
    worker = readAsset("game-worker.js")
    assert "const restored = await loadSavesFromIDB(" in worker
    assert (
        "if (restored) {\n            globalThis.syncSaves = makeSyncSaves(" in worker
    )
    assert worker.count("globalThis.syncSaves =") == 1
    assert "type: 'nosave'" in worker
    assert 'message.type === "nosave"' in readAsset("boot.js")


def test_the_game_worker_brings_its_own_blocking_sleep():
    # tak.ui.pyodide polls for input with time.sleep; Pyodide's own sleep
    # dies under a Safari user agent, so the worker replaces it.
    worker = readAsset("game-worker.js")
    assert "globalThis.takSleep" in worker
    assert "Atomics.wait(sleepCell, 0, 0, ms)" in worker
    assert "_time.sleep = _sleep" in worker
