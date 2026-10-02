# tak

A text-adventure kit. One user-interface contract with three front-ends behind it — a terminal, a browser that polls a server, and a browser tab that runs the whole game itself under [Pyodide](https://pyodide.org) — plus the pieces a menu-driven text game needs around it: numbered save slots with JSON Schema validation, a progressive-disclosure unlock engine, NPC dialogue with conditional lines, and clock formatting for a day-based header.

Extracted from [FishE](https://github.com/Stephenson-Software/FishE). [Tidewater](https://github.com/Stephenson-Software/Tidewater) is the first game built on it; FishE is being moved onto it.

## Install

```bash
pip install "tak @ git+https://github.com/Stephenson-Software/tak@v0.3.0"
```

Python 3.8+. The only dependency is `jsonschema`.

## What a game writes

[`examples/minimal_game.py`](examples/minimal_game.py) is the whole of it in 120 lines: three scenes, one villager with a line that unlocks, a header, no saves. `python3 examples/minimal_game.py` plays it in the terminal, `--web` in a browser. Copy it and replace the words.

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

The `header` callable is the game's status line, read fresh before every menu. Its `title` is the browser tab's title (the console ignores it); the page heading is the `title` given to `createUserInterface`. The kit knows nothing about days or money — chips are whatever the game says, and an optional `class` on a chip (`{"text": "Energy: 4/10", "class": "low"}`) lets the browser style it.

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

`chooseSlot(ui, saves, "Tidewater", describe)` runs the opening menu — load, new, delete, quit — and returns `("load", n)`, `("new", n)` or `None`. A slot whose primary file will not parse stays listed as damaged and stays claimed, so a new game is never pointed at an occupied directory.

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

`build` puts the game's `src/` and `schemas/` and the tak package itself into `web/game.zip` (an `extraFiles` entry that does not exist is skipped, with a warning on stderr naming it); `serve` sends the `Cross-Origin-Opener-Policy`/`Cross-Origin-Embedder-Policy` headers SharedArrayBuffer needs and serves the kit's assets at `/tak/`. Any proxy in front of it must preserve those headers.

## Running an existing console game in the browser

A small game that only uses `input()` and `print()` does not need rewriting
against the kit to be played in a browser. `tak.web.console` runs it **unmodified**
under Pyodide. It is on `main` and not yet in a tagged release, so install from `main`
rather than the tag above to use it:

- Its prompts and output go to a terminal on the page. The terminal uses a real text field, so a
  phone's keyboard, paste and autocorrect-off all work.
- `input()` blocks on the same SharedArrayBuffer ring as the kit's own front-end.
- `os.system("cls")` or `"clear"` and ANSI clear-screen codes clear the terminal. Other escape
  codes are dropped.
- Any file the program creates or changes under its directory (a save file, a `saves/` folder) is
  kept in the browser's IndexedDB and restored on the next visit.

```python
# web/build_zip.py
from tak.web.bundle import build
from tak.web.console import page
open("web/index.html", "w").write(
    page(title="Guess My Word", entry="src/guessMyWord.py", idbName="guess-my-word-files"))
build(ROOT, sourceDirectories=("src",), extraFiles=("version.txt",))
```

`entry` is run as `__main__` with its own directory as the working directory. `idbName` must be
unique per game and must never change once players have saves under it. The page loads
`/tak/console.css`, `/tak/console.js` and `/tak/console-worker.js`, which `tak.web.serve` and
[arcade](https://github.com/Stephenson-Software/arcade) both serve. Like every tak browser build,
it must be served cross-origin isolated.

## Development

```bash
pip install -e '.[dev]'
./test.sh
./format.sh
```

## License
This project is licensed under the **Stephenson Software Non-Commercial License (Stephenson-NC)**.  
© 2026 Daniel McCoy Stephenson. All rights reserved.  

You may use, modify, and share this software for **non-commercial purposes only**.  
Commercial use is prohibited without explicit written permission from the copyright holder.  

Full license text: [Stephenson-NC License](https://github.com/Stephenson-Software/stephenson-nc-license) (also in [LICENSE](LICENSE))  
SPDX Identifier: `Stephenson-NC`
