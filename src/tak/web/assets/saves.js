// Save export / import for both of tak's browser runtimes: tak's own
// front-end (boot.js) and the console runtime (console.js). Each of them loads
// this file and calls
//
//   TakSaves.attach({
//     idbName: 'tidewater-saves',  // the game's IndexedDB database
//     root: '/saves',              // every stored path is under root + '/'
//     after: element,              // the "Saves" bar is inserted after this
//     stop: function () { ... },   // stop the game and every save write
//     log: '[tidewater]',
//   });
//
// A save file is one JSON document:
//
//   {"format": "tak-saves", "version": 1, "game": "<idbName>",
//    "exported": "<ISO time>", "files": {"<path>": <content>, ...}}
//
// where <content> is the stored value itself when it is a string (tak's own
// front-end stores UTF-8 text) and {"base64": "..."} when it is bytes (the
// console runtime stores a Uint8Array). An import writes each value back as
// the same type, so a round trip is exact.
//
// -- Why nothing here can lose a save -----------------------------------------
// Both runtimes write the Worker's in-memory copy of every save over the
// stored one with every sync (boot.js idbWrite and console.js keepFiles; they
// never clear the store, and delete only files the game itself removed). A
// save imported behind a running game's back would therefore be overwritten
// by that game's next save of the same slot. So an import runs in this order:
//
//   1. The file is read and validated in full, and the change it would make
//      (added / replaced / kept) is computed and shown. Storage is not
//      touched; cancelling leaves everything as it was and the game running.
//   2. On confirm, stop() is called. The runtime sets a flag that makes every
//      later IndexedDB write of its own a no-op - checked when the write's
//      transaction is created, not when the message arrives, so a write that
//      was already opening the database is dropped too - and terminates the
//      Worker, so no further sync can even be posted. All of a runtime's
//      IndexedDB writes happen on this main thread, so from this point on
//      nothing in this tab writes to the store except the code below. IndexedDB
//      runs transactions on one store in the order they were created, so any
//      write created before the flag was set has finished before the reads
//      below see the store.
//   3. Other tabs of the same game are told over a BroadcastChannel to stop
//      the same way (their next sync would erase the import otherwise), and
//      given a moment to do so. A tab running a tak from before this file
//      cannot hear it; the README says to close other tabs first.
//   4. The store is read again. If it changed while the dialog was open (the
//      game saved), the new comparison is shown and confirmed again.
//   5. A backup of the store as it is now is written to a second database,
//      <idbName>.tak-backups (the last five imports are kept), and read back.
//      If that fails, nothing else happens: the store is untouched.
//      The backup is kept in the browser rather than downloaded automatically
//      because a write can be awaited and checked, while a download cannot (a
//      phone may ask where to save it, or silently not save it at all). The
//      panel offers each kept backup as a download, and loading that file
//      undoes the import (it puts back every file the import replaced).
//   6. The imported files are merged into the store in one transaction: a file
//      at the same path is replaced, every other stored file is KEPT. Nothing
//      is ever deleted by an import.
//   7. The store is read back and every imported file compared; then the page
//      reloads, and the new Worker restores from the store as on any visit.
//      Between 2 and the reload nothing can sync, so the next sync is from a
//      session that restored the imported files.
//
// Export only reads the store (a read-only transaction), on this thread, so it
// never stops or touches the game.

window.TakSaves = (function () {
  const FORMAT = "tak-saves";
  const FORMAT_VERSION = 1;
  const IDB_STORE = "files";
  const IDB_VERSION = 1;
  const BACKUP_SUFFIX = ".tak-backups";
  const BACKUP_STORE = "backups";
  const BACKUPS_KEPT = 5;
  const MAX_FILE_BYTES = 20 * 1024 * 1024;  // the size of a save file accepted for import
  const MAX_FILES = 5000;
  const MAX_PATH_LENGTH = 1024;

  // -- Pure helpers (no DOM, no storage) ---------------------------------------

  function bytesToBase64(bytes) {
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
  }

  function base64ToBytes(text) {
    if (!/^[A-Za-z0-9+/]*={0,2}$/.test(text) || text.length % 4 !== 0) {
      throw new Error("not base64");
    }
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function asBytes(value) {
    if (value instanceof Uint8Array) return value;
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    return null;
  }

  // A stored value as it goes into the file. Anything that is neither text nor
  // bytes fails the whole export rather than being left out of it quietly.
  function encodeValue(path, value) {
    if (typeof value === "string") return value;
    const bytes = asBytes(value);
    if (bytes) return { base64: bytesToBase64(bytes) };
    throw new Error("the stored file " + path + " is of a kind this export cannot write");
  }

  function sameValue(a, b) {
    if (typeof a === "string" || typeof b === "string") return a === b;
    const x = asBytes(a), y = asBytes(b);
    if (!x || !y || x.length !== y.length) return false;
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return false;
    return true;
  }

  // entries: [[path, value], ...] as read from the store.
  function buildExport(game, entries, now) {
    const files = {};
    for (const [path, value] of entries) files[path] = encodeValue(path, value);
    return {
      format: FORMAT,
      version: FORMAT_VERSION,
      game: game,
      exported: (now || new Date()).toISOString(),
      files: files,
    };
  }

  // Why a path may not be imported, or null if it may.
  function checkPath(path, root) {
    if (typeof path !== "string" || !path) return "an empty path";
    if (path.length > MAX_PATH_LENGTH) return "a path that is too long";
    if (!path.startsWith(root + "/")) return "a path outside " + root + "/";
    if (/[\u0000-\u001f\\]/.test(path)) return "a path with a control character or backslash";
    const parts = path.slice(root.length + 1).split("/");
    for (const part of parts) {
      if (part === "" || part === "." || part === "..") return "a path with an empty, . or .. part";
    }
    return null;
  }

  function friendlyGame(name) {
    return String(name).replace(/[-_.](saves|files)$/, "");
  }

  // Validate a whole save file without touching storage. Resolves nothing,
  // throws nothing: returns { ok: true, files: Map, exported } or
  // { ok: false, reason } with a sentence for the player.
  function parseImport(text, game, root) {
    const refuse = (reason) => ({ ok: false, reason: reason });
    if (typeof text !== "string") return refuse("The file could not be read.");
    if (text.length > MAX_FILE_BYTES) return refuse("The file is too large to be a saves file.");
    let doc;
    try { doc = JSON.parse(text); }
    catch (e) { return refuse("This is not a saves file (it is not readable JSON). Nothing was changed."); }
    if (!doc || typeof doc !== "object" || Array.isArray(doc) || doc.format !== FORMAT) {
      return refuse("This is not a tak saves file. Nothing was changed.");
    }
    if (typeof doc.version !== "number" || !Number.isInteger(doc.version)) {
      return refuse("The saves file has no readable version. Nothing was changed.");
    }
    if (doc.version > FORMAT_VERSION) {
      return refuse("The saves file was made by a newer version of the game; reload the page " +
                    "to get the newest version, then try again. Nothing was changed.");
    }
    if (doc.version !== FORMAT_VERSION) {
      return refuse("The saves file's version (" + doc.version + ") is not one this page reads. Nothing was changed.");
    }
    if (typeof doc.game !== "string" || !doc.game) {
      return refuse("The saves file does not say which game it is for. Nothing was changed.");
    }
    if (doc.game !== game) {
      return refuse("This file holds saves for another game (" + friendlyGame(doc.game) +
                    "), not this one (" + friendlyGame(game) + "). Nothing was changed.");
    }
    if (!doc.files || typeof doc.files !== "object" || Array.isArray(doc.files)) {
      return refuse("The saves file has no list of files. Nothing was changed.");
    }
    const paths = Object.keys(doc.files);
    if (paths.length === 0) return refuse("The saves file contains no saves. Nothing was changed.");
    if (paths.length > MAX_FILES) return refuse("The saves file holds too many files. Nothing was changed.");
    const files = new Map();
    for (const path of paths) {
      const problem = checkPath(path, root);
      if (problem) {
        return refuse("The saves file contains " + problem + " (" + String(path).slice(0, 80) +
                      "), so it was not loaded. Nothing was changed.");
      }
      const value = doc.files[path];
      if (typeof value === "string") { files.set(path, value); continue; }
      if (value && typeof value === "object" && !Array.isArray(value) &&
          Object.keys(value).length === 1 && typeof value.base64 === "string") {
        try { files.set(path, base64ToBytes(value.base64)); continue; }
        catch (e) { /* falls through to the refusal */ }
      }
      return refuse("The saves file is damaged: " + path + " cannot be read. Nothing was changed.");
    }
    return { ok: true, files: files, exported: typeof doc.exported === "string" ? doc.exported : "" };
  }

  // What an import of `incoming` (Map) would do to `current` (Map).
  function compare(current, incoming) {
    const result = { added: [], replaced: [], same: [], kept: [] };
    for (const [path, value] of incoming) {
      if (!current.has(path)) result.added.push(path);
      else if (sameValue(current.get(path), value)) result.same.push(path);
      else result.replaced.push(path);
    }
    for (const path of current.keys()) if (!incoming.has(path)) result.kept.push(path);
    for (const key of Object.keys(result)) result[key].sort();
    return result;
  }

  // -- IndexedDB ---------------------------------------------------------------

  function openDb(name, storeName) {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(name, IDB_VERSION);
      request.onupgradeneeded = (ev) => {
        const db = ev.target.result;
        if (!db.objectStoreNames.contains(storeName)) db.createObjectStore(storeName);
      };
      request.onsuccess = (ev) => resolve(ev.target.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("the save storage is busy in another tab"));
    });
  }

  function finish(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
      tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
    });
  }

  // Every [path, value] in the game's store, in one read-only transaction.
  async function readStore(idbName) {
    const db = await openDb(idbName, IDB_STORE);
    try {
      const tx = db.transaction(IDB_STORE, "readonly");
      const done = finish(tx);
      const entries = new Map();
      await new Promise((resolve, reject) => {
        const cursor = tx.objectStore(IDB_STORE).openCursor();
        cursor.onsuccess = (ev) => {
          const c = ev.target.result;
          if (c) { entries.set(c.key, c.value); c.continue(); } else resolve();
        };
        cursor.onerror = () => reject(cursor.error);
      });
      await done;
      return entries;
    } finally { db.close(); }
  }

  async function writeBackup(idbName, doc) {
    const db = await openDb(idbName + BACKUP_SUFFIX, BACKUP_STORE);
    try {
      const key = doc.exported;
      let tx = db.transaction(BACKUP_STORE, "readwrite");
      let done = finish(tx);
      tx.objectStore(BACKUP_STORE).put(doc, key);
      await done;
      // Read it back before anything relies on it.
      tx = db.transaction(BACKUP_STORE, "readonly");
      done = finish(tx);
      const stored = await new Promise((resolve, reject) => {
        const request = tx.objectStore(BACKUP_STORE).get(key);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      await done;
      if (!stored || JSON.stringify(stored.files) !== JSON.stringify(doc.files)) {
        throw new Error("the backup did not read back as written");
      }
      // Keep the newest few. Only ever after the new one is safely stored.
      const keys = await listBackupKeys(db);
      const old = keys.slice(0, Math.max(0, keys.length - BACKUPS_KEPT));
      if (old.length) {
        tx = db.transaction(BACKUP_STORE, "readwrite");
        done = finish(tx);
        for (const k of old) tx.objectStore(BACKUP_STORE).delete(k);
        await done;
      }
    } finally { db.close(); }
  }

  function listBackupKeys(db) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(BACKUP_STORE, "readonly");
      const request = tx.objectStore(BACKUP_STORE).getAllKeys();
      request.onsuccess = () => resolve(request.result.slice().sort());
      request.onerror = () => reject(request.error);
    });
  }

  async function readBackups(idbName) {
    const db = await openDb(idbName + BACKUP_SUFFIX, BACKUP_STORE);
    try {
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(BACKUP_STORE, "readonly");
        const request = tx.objectStore(BACKUP_STORE).getAll();
        request.onsuccess = () => resolve(request.result.slice().sort((a, b) =>
          a.exported < b.exported ? 1 : a.exported > b.exported ? -1 : 0));
        request.onerror = () => reject(request.error);
      });
    } finally { db.close(); }
  }

  // Merge: put every imported file; never clear, never delete.
  async function mergeIntoStore(idbName, files) {
    const db = await openDb(idbName, IDB_STORE);
    try {
      const tx = db.transaction(IDB_STORE, "readwrite");
      const done = finish(tx);
      const store = tx.objectStore(IDB_STORE);
      for (const [path, value] of files) store.put(value, path);
      await done;
    } finally { db.close(); }
  }

  // -- Downloads ---------------------------------------------------------------

  function dateStamp(iso) {
    return String(iso).slice(0, 10);
  }

  function download(doc, name) {
    const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(url); link.remove(); }, 60000);
  }

  function exportName(game, iso, kind) {
    return friendlyGame(game) + "-" + (kind || "saves") + "-" + dateStamp(iso) + ".json";
  }

  // -- The panel ---------------------------------------------------------------

  const STYLE = `
.tak-saves-bar { margin: .75rem 0; display: flex; justify-content: flex-end; }
.tak-saves-open, .tak-saves-dialog button {
  font: 16px system-ui, sans-serif; min-height: 44px; min-width: 44px;
  padding: .5rem 1rem; border-radius: 6px; cursor: pointer;
  border: 1px solid #2f5a72; background: #163345; color: #e0f0ff;
  touch-action: manipulation; -webkit-tap-highlight-color: transparent; width: auto; display: inline-block; text-align: center; margin: 0;
}
.tak-saves-open:hover, .tak-saves-dialog button:hover { background: #1f4a63; }
.tak-saves-dialog button.tak-saves-primary { background: #1d5a7a; border-color: #2f7ba0; }
.tak-saves-dialog button.tak-saves-danger { background: #4a1620; border-color: #7a2a35; }
.tak-saves-dialog button:disabled { opacity: .5; cursor: not-allowed; }
.tak-saves-dialog {
  font: 16px/1.45 system-ui, sans-serif; color: #e0f0ff; background: #0f2433;
  border: 1px solid #2f5a72; border-radius: 10px; padding: 1rem 1.1rem;
  width: min(34rem, calc(100vw - 32px)); max-height: calc(100vh - 32px);
  box-sizing: border-box; overflow-y: auto; overflow-wrap: anywhere;
}
.tak-saves-dialog::backdrop { background: rgba(0, 0, 0, .6); }
.tak-saves-dialog h3 { margin: 0 0 .5rem; font-size: 1.15rem; }
.tak-saves-dialog p { margin: .5rem 0; }
.tak-saves-dialog ul { margin: .25rem 0 .75rem; padding-left: 1.25rem; }
.tak-saves-actions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .75rem 0; }
.tak-saves-message { margin: .5rem 0; color: #9fd0ff; }
.tak-saves-message.error { color: #ff8a8a; }
.tak-saves-note { color: #9fb6c6; font-size: .9rem; }
@media (max-width: 600px) { .tak-saves-actions button { flex: 1 1 100%; } }
`;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function button(label, className, onClick) {
    const node = element("button", className, label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function relative(path, root) {
    return path.startsWith(root + "/") ? path.slice(root.length + 1) : path;
  }

  function attach(options) {
    const idbName = options.idbName;
    const root = (options.root || "/saves").replace(/\/+$/, "");
    const log = options.log || "[tak]";
    const stop = options.stop || function () {};
    let stopped = false;

    function stopGame() {
      if (stopped) return;
      stopped = true;
      try { stop(); } catch (e) { console.warn(log, "stopping the game failed:", e); }
    }

    if (!document.getElementById("tak-saves-style")) {
      const style = element("style");
      style.id = "tak-saves-style";
      style.textContent = STYLE;
      document.head.appendChild(style);
    }

    const bar = element("div", "tak-saves-bar");
    const open = button("Saves", "tak-saves-open", () => showMenu());
    open.setAttribute("aria-haspopup", "dialog");
    bar.appendChild(open);
    const dialog = element("dialog", "tak-saves-dialog");
    dialog.setAttribute("aria-labelledby", "tak-saves-title");
    const fileInput = element("input");
    fileInput.type = "file";
    fileInput.accept = ".json,application/json";
    fileInput.style.display = "none";
    fileInput.setAttribute("aria-hidden", "true");
    fileInput.tabIndex = -1;
    bar.append(fileInput, dialog);
    // The game's number-key and Enter/Space shortcuts listen on the document;
    // a key pressed in this panel must not also play a turn.
    bar.addEventListener("keydown", (ev) => ev.stopPropagation());
    if (options.after && options.after.parentNode) {
      options.after.parentNode.insertBefore(bar, options.after.nextSibling);
    } else {
      document.body.appendChild(bar);
    }

    let channel = null;
    try {
      channel = new BroadcastChannel("tak-saves:" + idbName);
      channel.onmessage = (ev) => {
        if (!ev.data || ev.data.type !== "import") return;
        stopGame();
        render([
          element("h3", null, "Saves were loaded in another tab"),
          element("p", null, "This game was stopped so it cannot overwrite them. Reload the page to keep playing with the loaded saves."),
        ], [button("Reload", "tak-saves-primary", () => location.reload())]);
        showDialog();
      };
    } catch (e) { /* no BroadcastChannel: single-tab safety still holds */ }

    function showDialog() {
      if (dialog.open) return;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }

    function close() {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }

    function render(nodes, actions) {
      dialog.textContent = "";
      const heading = nodes[0];
      if (heading) heading.id = "tak-saves-title";
      dialog.append(...nodes);
      if (actions && actions.length) {
        const row = element("div", "tak-saves-actions");
        row.append(...actions);
        dialog.appendChild(row);
      }
    }

    function message(text, isError) {
      const node = element("p", "tak-saves-message" + (isError ? " error" : ""), text);
      node.setAttribute("role", isError ? "alert" : "status");
      return node;
    }

    async function showMenu(notice, noticeIsError) {
      const nodes = [
        element("h3", null, "Your saves"),
        element("p", null, "Your saved games are kept in this browser only. Download them to keep a " +
          "copy, or to carry them to another browser or device and load them there."),
      ];
      if (notice) nodes.push(message(notice, noticeIsError));
      const actions = [
        button("Download my saves", "tak-saves-primary", exportSaves),
        button("Load saves from a file", null, () => { fileInput.value = ""; fileInput.click(); }),
        button("Close", null, close),
      ];
      const backupNodes = [];
      try {
        const backups = await readBackups(idbName);
        if (backups.length) {
          backupNodes.push(element("p", "tak-saves-note",
            "Before each load, the saves this browser had were kept as a backup. Loading a backup " +
            "file puts back every save that load replaced."));
          const list = element("div", "tak-saves-actions");
          for (const backup of backups) {
            list.appendChild(button("Download backup from " + backup.exported.replace("T", " ").slice(0, 16),
              null, () => download(backup, exportName(idbName, backup.exported, "backup"))));
          }
          backupNodes.push(list);
        }
      } catch (e) { console.warn(log, "could not list save backups:", e); }
      if (stopped) {
        nodes.push(message("The game was stopped. Reload the page to keep playing.", true));
        actions.splice(1, 1);  // nothing can be loaded into a stopped page but by reloading
        actions.unshift(button("Reload", null, () => location.reload()));
      }
      render(nodes.concat(backupNodes), actions);
      showDialog();
    }

    async function exportSaves() {
      let entries;
      try { entries = await readStore(idbName); }
      catch (e) {
        console.warn(log, "export failed:", e);
        showMenu("Your saves could not be read just now, so nothing was downloaded. Reloading the page usually fixes this.", true);
        return;
      }
      if (entries.size === 0) { showMenu("There are no saves in this browser yet."); return; }
      let doc;
      try { doc = buildExport(idbName, entries); }
      catch (e) { showMenu("Your saves could not be written to a file: " + e.message, true); return; }
      download(doc, exportName(idbName, doc.exported));
      showMenu("Downloaded " + entries.size + " file(s) as " + exportName(idbName, doc.exported) + ".");
    }

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      if (file.size > MAX_FILE_BYTES) { showMenu("That file is too large to be a saves file. Nothing was changed.", true); return; }
      let text;
      try { text = await file.text(); }
      catch (e) { showMenu("That file could not be read. Nothing was changed.", true); return; }
      const parsed = parseImport(text, idbName, root);
      if (!parsed.ok) { showMenu(parsed.reason, true); return; }
      let current;
      try { current = await readStore(idbName); }
      catch (e) { showMenu("Your current saves could not be read, so nothing was loaded (loading now could not keep them safe). Nothing was changed.", true); return; }
      confirmImport(parsed, compare(current, parsed.files), false);
    });

    function pathList(title, paths) {
      if (!paths.length) return [];
      const list = element("ul");
      for (const path of paths.slice(0, 12)) list.appendChild(element("li", null, relative(path, root)));
      if (paths.length > 12) list.appendChild(element("li", null, "…and " + (paths.length - 12) + " more"));
      return [element("p", null, title), list];
    }

    function confirmImport(parsed, change, again) {
      const nodes = [element("h3", null, "Load these saves?")];
      if (again) nodes.push(message("Your saves changed while this was open (the game saved). This is the up-to-date list.", true));
      if (!change.added.length && !change.replaced.length) {
        render(nodes.concat([message("Every save in this file is already here, exactly the same. Nothing needs to change.")]),
          [button(stopped ? "Reload" : "Close", null, stopped ? () => location.reload() : close)]);
        showDialog();
        return;
      }
      nodes.push(...pathList("Added (" + change.added.length + "):", change.added));
      nodes.push(...pathList("Replaced by the copy in the file (" + change.replaced.length + "):", change.replaced));
      nodes.push(...pathList("Kept as they are (" + change.kept.length + "):", change.kept));
      if (change.same.length) nodes.push(element("p", "tak-saves-note", change.same.length + " file(s) in it are already here unchanged."));
      nodes.push(element("p", "tak-saves-note",
        "Nothing is deleted. A backup of your saves as they are now is kept in this browser first. " +
        "The game then restarts; progress since your last save is not kept."));
      render(nodes, [
        button("Load and restart", change.replaced.length ? "tak-saves-danger" : "tak-saves-primary",
               () => applyImport(parsed, change)),
        button("Cancel", null, stopped ? () => location.reload() : close),
      ]);
      showDialog();
    }

    function failed(text) {
      render([element("h3", null, "Saves were not loaded"), message(text, true)],
        [button("Reload", "tak-saves-primary", () => location.reload())]);
      showDialog();
    }

    async function applyImport(parsed, shown) {
      for (const b of dialog.querySelectorAll("button")) b.disabled = true;
      // Steps 2 and 3 of the order at the top of this file.
      stopGame();
      if (channel) {
        try { channel.postMessage({ type: "import" }); } catch (e) {}
        // A moment for the other tabs to take the message and for any write
        // one of them had already started to finish before the store is read.
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
      let current;
      try { current = await readStore(idbName); }
      catch (e) { failed("Your current saves could not be read, so nothing was loaded. Nothing was changed."); return; }
      const change = compare(current, parsed.files);
      if (JSON.stringify([change.added, change.replaced]) !== JSON.stringify([shown.added, shown.replaced])) {
        confirmImport(parsed, change, true);
        return;
      }
      if (current.size) {
        try { await writeBackup(idbName, buildExport(idbName, current)); }
        catch (e) {
          console.warn(log, "backup failed:", e);
          failed("A backup of your current saves could not be kept, so nothing was loaded. Nothing was changed.");
          return;
        }
      }
      try {
        await mergeIntoStore(idbName, parsed.files);
        const after = await readStore(idbName);
        for (const [path, value] of parsed.files) {
          if (!after.has(path) || !sameValue(after.get(path), value)) throw new Error(path + " did not read back");
        }
        for (const path of current.keys()) {
          if (!after.has(path)) throw new Error(path + " went missing");
        }
      } catch (e) {
        console.warn(log, "import failed:", e);
        failed("The saves could not be written completely (" + e.message + "). Your saves from just " +
               "before are in the backup offered under Saves after reloading.");
        return;
      }
      render([element("h3", null, "Saves loaded"), message("Restarting the game…")], []);
      location.reload();
    }

    return { showMenu: showMenu, element: bar };
  }

  return {
    attach: attach,
    // Exposed for tests.
    _parseImport: parseImport,
    _buildExport: buildExport,
    _compare: compare,
    _checkPath: checkPath,
    _sameValue: sameValue,
  };
})();
