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

`chooseSlot(ui, saves, "Tidewater", describe)` runs the opening menu — load, new, delete, quit — and returns `("load", n)`, `("new", n)` or `None`. A slot whose primary file will not parse stays listed as damaged and stays claimed, so a new game is never pointed at an occupied directory. `get_next_available_slot()` decides "free" from the disk, not from the list: any `slot_N` directory holding a file is taken even if the menu could not show it, and `chooseSlot` refuses to start a new run in a slot that holds files.

In the browser, each sync writes the save directory's files to IndexedDB in one all-or-nothing transaction and never clears the store. A stored file is deleted only when this tab restored or saved it and the game then removed it (deleting a slot, a migration moving a file); a stored save the tab never had, such as one written by another tab, is left alone.

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

## Saves: download and load

Both browser runtimes put a small **Saves** button under the game. It opens a panel with
**Download my saves** and **Load saves from a file**. A browser's saves live only in that
browser, so this is how a player keeps a copy, moves to another device or browser, or follows a
game to a new address. Neither page has to change: `boot.js` and `console.js` load
`/tak/saves.js` themselves. Set `savesUrl` in the start config if it is served somewhere else.

**The file** is one JSON document named `<game>-saves-YYYY-MM-DD.json`:

```json
{"format": "tak-saves", "version": 1, "game": "tidewater-saves",
 "exported": "2026-10-03T12:00:00.000Z",
 "files": {"/saves/slot_1/save.json": "{...}", "/game/src/saves/a.bin": {"base64": "AP8K"}}}
```

`game` is the page's `idbName`. Each file is stored as it was kept: text stays a string (tak's own
front-end), and bytes become `{"base64": …}` (the console runtime). Download only reads the
browser's storage, so the game keeps running.

**Loading a file** never loses a save:

1. **Validation before anything is written.** The whole file is checked before storage is touched.
   A file is refused, with nothing changed, if it is not a tak saves file, if its version is newer,
   or if it is another game's file ("This file holds saves for another game (overwinter), not this
   one (tidewater)"). It is also refused if it holds a path outside the runtime's save root
   (`/saves/` for tak's front-end, `/game/` for the console runtime), a `..` or empty path part,
   or damaged content, or if it is over 20 MB.
2. **Confirmation.** The panel lists the files that will be **added**, **replaced** by the copy
   in the file, and **kept** as they are. Nothing is written until the player confirms.
3. **Stop, then write.** On confirm, the game is stopped first. The runtime terminates its
   Worker, and from then on it ignores any save write of its own. Each runtime writes its own
   copy of every save on every sync, so a running game would otherwise overwrite an imported
   slot on its next save. Other open tabs of the same game are told to stop too.
4. **Backup first.** The saves as they are at that moment are copied to a second IndexedDB
   database, `<idbName>.tak-backups`, and read back. The last five are kept, and the panel
   offers each one as a download. If the backup fails, nothing else happens. Loading a backup
   file puts back every save the import replaced.
5. **Merge, never delete.** Each imported file replaces the file at the same path. Every other
   save is kept. The result is read back and checked, then the page reloads and the game
   restores from storage as on any visit.

A tab still running a tak release from before this feature cannot be told to stop, so close
other tabs of the game before loading saves.

## Cloud saves on arcade

A game on arcade (`https://<slug>.play.danielstephenson.dev`) can let a signed-in player back
its saves up to their arcade-social account and get them on their other devices
(Stephenson-Software RFC 0016). It is opt-in twice: the game sets `cloudSaves: true` in
`TakBoot.start`, and arcade-social must list the game in the gateway's `config/play/saves.yaml`.
Then the **Saves** panel offers **Turn on cloud backup** to a signed-in player. Nothing is sent
until the player turns it on, anywhere else (localhost, an alias host, a desktop build) nothing is
loaded at all, and the console runtime does not support it.

```js
TakBoot.start({ idbName: "night-ferry-saves", /* ... */ cloudSaves: true });
```

`boot.js` then loads `/tak/cloud.js` (`cloudUrl` to override). The game still saves to IndexedDB
first, exactly as before, and the cloud is never in the save path:

- **Backup (Stage 1).** After each committed save (at most one upload per 30 s) the store is
  uploaded as a save file, based on the version this browser last uploaded or pulled. The server
  refuses anything not based on its newest version (409); the page then merges **per slot**, and a
  slot changed on two devices is **kept twice**, the second copy going into the next free
  `slot_N`. Nothing is ever newest-wins. **Saves → Cloud versions** lists every version, each with
  **Load this version** (the file import above, with its preview, backup and reload) and
  **Download**.
- **Sync (Stage 2, when `saves.yaml` sets `pull: true`).** In a browser that turned it on, a
  sync runs before the game starts (at most about 3 s; a slow network delays nothing longer). If
  the account has newer saves, they come down **as an import**: validated, the store backed up
  into `<idbName>.tak-backups` and read back first, written put-only (no local file is ever
  deleted), read back, then the page reloads once. A pull never begins after the game has started.
- **Errors are never "empty".** A failure of any kind (offline, signed out, a 5xx, the kill switch)
  is "unknown": nothing is uploaded or written because of it, and the panel says "Not backed up".
- **Lost slots are carried, not dropped.** A slot missing from this browser that the game did not
  delete is kept in the cloud from the last version, restored here at the next load, and if a new
  game is started in that slot meanwhile, both are kept.
- **Deletions stay local.** A slot deleted in the game is deleted in the cloud's newest version
  (history keeps it), but never on another device: it stays there until deleted there too.
- **A session whose restore failed uploads nothing.**

`cloud.js`'s engine is tested under Node by `tests/web/test_cloud_saves.py`, including a
randomized three-device test (`tests/web/cloud_sim.js`) against an in-memory server with
arcade-social's rules; arcade-social keeps a Python copy of the same algorithm in its own tests.

## Scores and achievements on arcade

`tak.arcade` reports a player's scores and achievement unlocks to
[arcade-social](https://github.com/Stephenson-Software/arcade-social) (`https://api.play.danielstephenson.dev`),
which shows them as leaderboards and "N% of players" on the game's page at
[danielstephenson.dev/play](https://danielstephenson.dev/play) (Stephenson-Software RFC 0014):

```python
from tak import arcade

arcade.submitScore("most-money", 12450)            # returns None; never raises
arcade.submitScore("fastest-crossing", 41.7, run="seed-12")   # run: optional tag, 1-64 of A-Za-z0-9._:-
arcade.unlock("first-catch")                       # idempotent; never raises
```

- **Declare first.** Boards and achievements are declared in the gateway's `config/play/boards.yaml`
  (ids `^[a-z][a-z0-9-]{1,30}$`, never renamed once used; each board has `min`/`max`). The service
  refuses anything undeclared or out of bounds.
- **Call unconditionally.** Outside the browser (console, the HTTP front-end, tests) both calls do
  nothing. In the browser the Worker posts each report to the page (`{type: "arcade"}`), and
  `boot.js` loads `/tak/arcade.js` on the first one, which sends it from the main thread with the
  player's sign-in cookie. Nothing is ever waited for and nothing is thrown into the game.
- **Silently dropped** when the page is not `https://<slug>.play.danielstephenson.dev` (an alias such
  as `fishe.danielstephenson.dev`, `localhost`, a desktop build), when the player is not signed in
  (checked once a minute at `/v1/session`; a score earned signed out is never uploaded later), when
  the service refuses it (undeclared, out of bounds, no display name yet, `maxPerHour`), or after one
  retry 30 s later when the service is down or unreachable. Malformed calls (a bad id, a non-finite
  value) are ignored.
- **Send every score.** The client does not know the player's stored best, so it does not filter;
  the service keeps each player's best per board.
- **Scores are forgeable.** They come from the player's browser, and every leaderboard is labelled
  "not verified". Do not attach anything of value to them.
- The console runtime (unmodified console programs) has no `takArcade` bridge, so the calls are
  no-ops there. Set `arcadeUrl` in `TakBoot.start` if `arcade.js` is served somewhere other than
  `/tak/arcade.js`.

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
