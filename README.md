# tak

A text-adventure kit. One user-interface contract with three front-ends behind it — a terminal, a browser that polls a server, and a browser tab that runs the whole game itself under [Pyodide](https://pyodide.org) — plus the pieces a menu-driven text game needs around it: numbered save slots with JSON Schema validation, a progressive-disclosure unlock engine, NPC dialogue with conditional lines, and clock formatting for a day-based header.

Extracted from [FishE](https://github.com/Stephenson-Software/FishE). [Tidewater](https://github.com/Stephenson-Software/Tidewater) is the first game built on it; FishE is the second.

## Install

```bash
pip install "tak @ git+https://github.com/Stephenson-Software/tak@v0.1.0"
```

Python 3.8+. The only dependency is `jsonschema`.

## What a game writes

A game is a synchronous loop that talks to the player through `BaseUserInterface`'s primitives and never imports a concrete front-end:

```python
from tak import Prompt
from tak.ui import UIType, createUserInterface

prompt = Prompt("You wake on the docks.")
header = lambda: {"title": "Tidewater — Loop 1", "chips": ["Loop 1", "8:00 AM"]}
ui = createUserInterface(UIType.CONSOLE, prompt, header, title="Tidewater", envPrefix="TIDEWATER")

choice = ui.showOptions("The Docks", ["Fish", "Go to the tavern"], unavailableOptions={2: "it's not open yet"})
ui.showDialogue("The tide is out.")
name = ui.promptForText("What is your name?")
ui.cleanup()
```

| Primitive | What it does |
|---|---|
| `showOptions(descriptor, options, unavailableOptions=None)` | Numbered menu; returns the chosen number as a string. Unavailable rows are listed but greyed with their reason. |
| `showDialogue(text)` | A block of text and a Continue. |
| `promptForText(text)` / `promptForNumber(text)` | Free-text / numeric input. |
| `showBusy(message, seconds)` | A pause with a message, no input. |
| `timedKeyPress(message)` | Seconds until the player reacts. |
| `showInteractiveDialogue(npc)` | A conversation menu built from an `NPC`'s options. |

The `header` callable is the game's status line, read fresh before every menu. The kit knows nothing about days or money — chips are whatever the game says, and an optional `class` on a chip (`{"text": "Energy: 4/10", "class": "low"}`) lets the browser style it.

### Front-ends

| `UIType` | Where the game runs | Saves live |
|---|---|---|
| `CONSOLE` | the terminal | on disk |
| `WEB` | a Python process; the browser is a terminal for it (`<PREFIX>_WEB_HOST`/`_PORT`) | on the server's disk, shared by every visitor |
| `PYODIDE` | inside the player's browser tab | in that browser's IndexedDB |

### Saves

```python
from tak.saves import SaveFileManager, validateAgainstSchema, syncBrowserSaves

saves = SaveFileManager("data", primaryFile="save.json",
                        readMetadata=lambda slotPath, data: {"loop": data["loop"]})
slot = saves.get_next_available_slot()
saves.select_save_slot(slot)
validateAgainstSchema(state, "schemas/save.json")
with open(saves.get_save_path("save.json"), "w") as f: json.dump(state, f)
syncBrowserSaves()   # no-op outside the browser; required after every write under Pyodide
```

A slot whose primary file will not parse stays listed as damaged and stays claimed, so a new game is never pointed at an occupied directory.

### Unlocks and NPCs

```python
from tak import Progression, NPC

progression = Progression([
    {"id": "tavern", "name": "the tavern", "announcement": "Old Tom's door is open to you.",
     "condition": lambda state: "tom_knows_you" in state.facts},
])
unlock = progression.getNextUnlock(state, state.unlocked)   # one per call; appended to the list you own

tom = NPC("Old Tom", "Keeps the tavern.", [
    {"question": "About the storm...", "response": "How could you know that?",
     "condition": lambda: "storm" in state.facts},
])
```

## Shipping a browser build

The kit carries the browser side: `client.js`/`client.css` (the renderer both web front-ends share), `boot.js` (SharedArrayBuffer input ring, IndexedDB save mirror, Worker wiring) and `game-worker.js` (loads Pyodide, restores saves, unpacks the bundle, runs the game). A game adds three small files:

**`web/index.html`**
```html
<link rel="stylesheet" href="/tak/client.css">
<div id="status"></div><div id="app"></div>
<script src="/tak/client.js"></script>
<script src="/tak/boot.js"></script>
<script>TakBoot.start({ idbName: "tidewater-saves", saveDirEnv: "TIDEWATER_SAVE_DIR" });</script>
```

**`web/pyodide_main.py`** — builds the game with `UIType.PYODIDE` and plays it.

**`web/build_zip.py`** and **`web/serve.py`**
```python
from tak.web.bundle import build;  build(ROOT, extraFiles=("version.txt", "web/pyodide_main.py"))
from tak.web.serve import main;    main(ROOT, title="Tidewater", envPrefix="TIDEWATER")
```

`build` puts the game's `src/` and `schemas/` and the tak package itself into `web/game.zip`; `serve` sends the `Cross-Origin-Opener-Policy`/`Cross-Origin-Embedder-Policy` headers SharedArrayBuffer needs and serves the kit's assets at `/tak/`. Any proxy in front of it must preserve those headers.

## Development

```bash
pip install -e '.[dev]'
./test.sh
./format.sh
```

## License

[Stephenson-NC](LICENSE) — non-commercial use.
