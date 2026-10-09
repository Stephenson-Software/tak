import json
import re

import pytest

import tak.web
from tak.web.console import page


def _config(html):
    match = re.search(r"TakConsole\.start\((\{.*?\})\);", html, re.S)
    assert match, html
    return json.loads(match.group(1).replace("<\\/", "</"))


def test_the_page_loads_the_console_runtime_and_names_the_entry():
    html = page(title="Guess My Word", entry="src/guessMyWord.py", idbName="gmw-files")
    assert '<link rel="stylesheet" href="/tak/console.css">' in html
    assert '<script src="/tak/console.js"></script>' in html
    assert "<title>Guess My Word</title>" in html
    config = _config(html)
    assert config["entry"] == "src/guessMyWord.py"
    assert config["idbName"] == "gmw-files"
    assert config["bundleUrl"] == "/web/game.zip"
    assert config["elementId"] == "console"
    assert config["packages"] == []


def test_title_tagline_and_config_cannot_break_out_of_the_page():
    html = page(title="<b>x</b>", entry="main.py", idbName="x", tagline='"quoted"')
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert "&quot;quoted&quot;" in html
    script = page(
        title="</script><script>alert(1)</script>", entry="main.py", idbName="x"
    )
    inline = script.split("TakConsole.start(", 1)[1]
    assert "</script><script>" not in inline.split("</script>")[0]
    assert _config(script)["logPrefix"] == "[</script><script>alert(1)</script>]"


@pytest.mark.parametrize("entry", ["", "/abs/main.py", "../main.py", "src/../../x.py"])
def test_entry_must_be_inside_the_bundle(entry):
    with pytest.raises(ValueError):
        page(title="t", entry=entry, idbName="x")


def test_idb_name_is_required():
    with pytest.raises(ValueError):
        page(title="t", entry="main.py", idbName="")


def test_packages_and_bundle_url_pass_through():
    config = _config(
        page(
            title="t",
            entry="main.py",
            idbName="x",
            packages=("numpy",),
            bundleUrl="/b.zip",
        )
    )
    assert config["packages"] == ["numpy"]
    assert config["bundleUrl"] == "/b.zip"


@pytest.mark.parametrize("name", ["console.js", "console.css", "console-worker.js"])
def test_the_runtime_assets_ship_with_the_package(name):
    assert tak.web.readAsset(name)


def test_the_worker_and_page_agree_on_the_message_protocol():
    worker = tak.web.readAsset("console-worker.js")
    client = tak.web.readAsset("console.js")
    for kind in (
        "status",
        "out",
        "err",
        "clear",
        "waiting",
        "files",
        "nosave",
        "exit",
        "error",
    ):
        assert "type: '%s'" % kind in worker, kind
        assert 'case "%s"' % kind in client, kind
    assert 'new Worker(config.workerUrl || "/tak/console-worker.js")' in client


def test_a_failed_restore_never_syncs():
    # The page replaces the whole store with each sync, so a sync after a
    # failed read would erase the player's saves. The worker must only build
    # its file sync when the restore succeeded, and must keep restored files
    # whatever their timestamps say.
    worker = tak.web.readAsset("console-worker.js")
    assert "if (restore.ok) {" in worker
    assert "syncFiles = makeFileSync(pyodide, pristine, restore.paths, log);" in worker
    assert worker.count("makeFileSync(") == 2  # the definition and the one guarded call
    assert "restored.has(path) ||" in worker


def test_the_runtime_brings_its_own_blocking_sleep():
    # Pyodide's own sleep path dies with a SuspendError under a Safari user
    # agent; the console runtime replaces time.sleep with Atomics.wait.
    worker = tak.web.readAsset("console-worker.js")
    assert "globalThis.takConsoleSleep" in worker
    assert "Atomics.wait(sleepCell, 0, 0, ms)" in worker
    assert "_time.sleep = _sleep" in worker
