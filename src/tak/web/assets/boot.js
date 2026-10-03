// The main-thread side of a tak game's Pyodide build. A game's index.html
// loads client.js, then this, then calls
//
//   TakBoot.start({
//     idbName: 'mygame-saves',      // IndexedDB database for this game's saves
//     saveDirEnv: 'MYGAME_SAVE_DIR',// the variable the game's config reads
//     bundleUrl: '/web/game.zip',   // built by tak.web.bundle
//     entry: 'web/pyodide_main.py', // inside the bundle
//     messages: { download: 'Downloading the village…' },  // optional
//     statusElementId: 'status',    // optional; where boot progress is shown
//   });
//
// It also puts a "Saves" control under the game (saves.js, loaded from
// config.savesUrl, default /tak/saves.js): download the saves as a file, and
// load them from one. A game's tak.arcade scores and unlocks are sent on by
// arcade.js (config.arcadeUrl, default /tak/arcade.js), loaded only when the
// game first reports one.
//
// Everything below is game-agnostic: the SharedArrayBuffer ring the player's
// input travels over, the IndexedDB mirror of the save directory, and the
// Worker that runs Python. See game-worker.js for the other half.

window.TakBoot = (function () {
  const RING_SIZE = 8192;
  const IDB_STORE   = "files";
  const IDB_VERSION = 1;

  function start(config) {
    config = config || {};
    const log = config.logPrefix || "[tak]";
    const statusEl = document.getElementById(config.statusElementId || "status");

    function setStatus(msg, isError) {
      if (!statusEl) return;
      statusEl.textContent = msg;
      statusEl.style.display = msg ? "" : "none";
      statusEl.classList.toggle("error", !!isError);
    }

    const idbName = config.idbName || "tak-saves";
    const saveDir = config.saveDir || "/saves";

    // -- Save export / import (saves.js) ---------------------------------------
    // Set once a save file is being loaded: from then on this page writes
    // nothing to IndexedDB of its own accord (idbWrite checks it when its
    // transaction is created) and the Worker is gone, so no sync can erase
    // what the import writes before the page reloads. See saves.js.
    let savesFrozen = false;
    let worker = null;
    function stopForSaveTransfer() {
      savesFrozen = true;
      if (worker) worker.terminate();
      setStatus("The game was stopped to load your saves.", false);
    }
    // Added before anything that can refuse to start the game, so that saves
    // stored earlier can still be downloaded from a browser that cannot run it.
    loadSaveTransfer(config.savesUrl || "/tak/saves.js", function () {
      window.TakSaves.attach({
        idbName: idbName,
        root: saveDir,
        after: document.getElementById("app") || statusEl,
        stop: stopForSaveTransfer,
        log: log,
      });
    }, log);

    // -- Input transport: SharedArrayBuffer ring buffer ----------------------
    //
    // The game loop is synchronous, so the Worker is blocked whenever the game
    // is waiting for the player - and a blocked Worker can never run an
    // onmessage handler. Shared memory needs no event loop, so it is how input
    // reaches a blocked game. sabMeta holds [writeIndex, readIndex]; this side
    // only ever advances writeIndex, Python only ever advances readIndex.
    //
    // SharedArrayBuffer requires the page to be cross-origin isolated, which
    // is what tak.web.serve's COOP/COEP headers are for.
    if (typeof SharedArrayBuffer === "undefined") {
      setStatus(
        "This browser can't run the game here: SharedArrayBuffer is unavailable, " +
        "which usually means the page wasn't served with the " +
        "Cross-Origin-Opener-Policy and Cross-Origin-Embedder-Policy headers " +
        "the game's server sets. Open the game over https (or http://localhost) " +
        "from that server rather than from a file:// path or a plain static host.",
        true
      );
      return;
    }

    const sab     = new SharedArrayBuffer(8 + RING_SIZE);
    const sabMeta = new Int32Array(sab, 0, 2);
    const sabData = new Uint8Array(sab, 8, RING_SIZE);

    function writeToRing(text) {
      const bytes = new TextEncoder().encode(text + "\n");
      const writeIndex = Atomics.load(sabMeta, 0);
      const readIndex  = Atomics.load(sabMeta, 1);
      if (bytes.length > RING_SIZE - (writeIndex - readIndex)) {
        // Only reachable if the game stopped draining input; overwriting would
        // corrupt the message it is mid-way through reading.
        console.warn(log, "input dropped: the ring buffer is full");
        return;
      }
      for (let i = 0; i < bytes.length; i++) {
        sabData[(writeIndex + i) % RING_SIZE] = bytes[i];
      }
      Atomics.store(sabMeta, 0, writeIndex + bytes.length);
    }

    // JSON-encoded so that whatever the player typed - a name with a newline
    // pasted into it, say - can't be read as two messages.
    TakClient.init(function (value) {
      writeToRing(JSON.stringify({ type: "input", value: value }));
    });

    // -- Save persistence: IndexedDB writes happen here, not in the Worker ---
    // The Worker is blocked by Python whenever the game runs, so it can't
    // drive IDB callbacks; it posts the save files over and this event loop
    // stores them. See the comment at the top of game-worker.js.
    function idbOpen() {
      return new Promise((resolve, reject) => {
        const req = indexedDB.open(idbName, IDB_VERSION);
        req.onupgradeneeded = (ev) => {
          const db = ev.target.result;
          if (!db.objectStoreNames.contains(IDB_STORE)) {
            db.createObjectStore(IDB_STORE);
          }
        };
        req.onsuccess = (ev) => resolve(ev.target.result);
        req.onerror   = (ev) => reject(ev.target.error);
      });
    }

    // Writes the files the Worker sent and deletes only the ones it names as
    // deleted (see mirrorToStore). Never clears the store.
    function idbWrite(files, deleted) {
      if (savesFrozen) return;
      idbOpen().then((db) => {
        // Checked again here, where the transaction is created: a save file
        // may have started loading while the database was opening.
        if (savesFrozen) { db.close(); return; }
        let tx;
        try { tx = db.transaction(IDB_STORE, "readwrite"); }
        catch (e) { console.warn(log, "could not save to IndexedDB:", e); db.close(); return; }
        tx.oncomplete = () => db.close();
        tx.onerror    = () => { console.warn(log, "save failed:", tx.error); db.close(); };
        tx.onabort    = () => { console.warn(log, "save not written, stored saves unchanged:", tx.error); db.close(); };
        mirrorToStore(tx.objectStore(IDB_STORE), files, deleted, log);
      }).catch((err) => console.warn(log, "could not open save storage:", err));
    }

    // -- Scores and achievements (arcade.js) -----------------------------------
    // Fire-and-forget: loaded on the game's first report, and if it cannot be
    // loaded the reports are dropped and the game never notices.
    let arcadeQueue = [];
    function arcade(request) {
      if (arcadeQueue === null) {
        if (window.TakArcade) window.TakArcade.handle(request);
        return;
      }
      if (arcadeQueue.length < 50) arcadeQueue.push(request);
      if (arcadeQueue.length > 1) return;
      loadScript(config.arcadeUrl || "/tak/arcade.js", function () {
        const queued = arcadeQueue || [];
        arcadeQueue = null;
        if (!window.TakArcade) return;
        queued.forEach(function (queuedRequest) { window.TakArcade.handle(queuedRequest); });
      }, log);
    }

    // -- Worker ----------------------------------------------------------------
    worker = new Worker(config.workerUrl || "/tak/game-worker.js");
    worker.onmessage = (e) => {
      const message = e.data;
      if (typeof message === "string") {
        let frame;
        try { frame = JSON.parse(message); }
        catch (err) { console.warn(log, "unreadable frame:", err); return; }
        if (frame.type === "screen") {
          setStatus("");           // the game is up; stop showing boot progress
          TakClient.render(frame.screen);
        }
        return;
      }
      if (message.type === "status") { setStatus(message.msg); return; }
      if (message.type === "ready")  { return; }
      if (message.type === "save")   { idbWrite(message.files, message.deleted); return; }
      if (message.type === "arcade") { arcade(message.request); return; }
      if (message.type === "nosave") {
        // A notice of its own, above the game: the status line is cleared as
        // soon as the first screen renders, and this must stay visible.
        const notice = document.createElement("div");
        notice.className = "status error tak-nosave";
        notice.setAttribute("role", "alert");
        notice.textContent = message.msg;
        const app = document.getElementById("app");
        if (app && app.parentNode) app.parentNode.insertBefore(notice, app);
        else document.body.insertBefore(notice, document.body.firstChild);
        return;
      }
      if (message.type === "error")  {
        setStatus("The game stopped: " + message.msg + " — reload the page to start again. " +
                  "Your saved games are stored in this browser and are not affected.", true);
      }
    };
    worker.onerror = (e) => {
      setStatus("The game could not be started: " + (e.message || "unknown error") +
                " — reload the page to try again.", true);
    };
    worker.postMessage({
      type: "init",
      sab: sab,
      ringSize: RING_SIZE,
      config: {
        idbName: idbName,
        saveDir: saveDir,
        saveDirEnv: config.saveDirEnv || "TAK_SAVE_DIR",
        bundleUrl: config.bundleUrl || "/web/game.zip",
        entry: config.entry || "web/pyodide_main.py",
        packages: config.packages || [],
        pyodideUrl: config.pyodideUrl,
        messages: config.messages || {},
        logPrefix: log,
      },
    });
  }

  // One sync, applied to the store inside the caller's readwrite transaction.
  //
  // Every file sent is written. A stored file NOT sent is deleted only if
  // `deleted` names it - the Worker names only files this session saw and the
  // game then removed (a slot deleted in the menu). Anything else in the store
  // is kept: a save written by another tab, a file the Worker could not read,
  // a path this session never restored. The store used to be cleared and
  // rewritten from the Worker's copy on every sync, so a save missing from
  // that copy for any reason was erased by the next save; it no longer can
  // be. A Worker that sends no `deleted` (one from an older tak) deletes
  // nothing. The console runtime (console.js) applies its syncs the same way.
  function mirrorToStore(store, files, deleted, log) {
    // All or nothing: the caller's one transaction either takes every write
    // and delete or none. A put that throws aborts it here, and IndexedDB
    // aborts it by itself if a request fails later (quota, say), so the store
    // is never left with part of a sync - one file of a slot new and the next
    // old, or a file skipped.
    try {
      for (const [path, content] of Object.entries(files)) store.put(content, path);
      for (const path of Array.isArray(deleted) ? deleted : []) {
        if (typeof path !== "string" || Object.prototype.hasOwnProperty.call(files, path)) continue;
        store.delete(path);
      }
      return true;
    } catch (e) {
      console.warn(log, "nothing was saved this time (the stored saves are unchanged):", e);
      try { store.transaction.abort(); } catch (abortError) { /* already over */ }
      return false;
    }
  }


  // saves.js is shared with the console runtime and fetched on demand, so a
  // game's index.html does not have to list it. If it cannot be loaded the
  // game runs exactly as before, just without the Saves control.
  function loadSaveTransfer(url, ready, log) {
    if (window.TakSaves) { ready(); return; }
    const script = document.createElement("script");
    script.src = url;
    script.onload = () => {
      if (window.TakSaves) ready();
    };
    script.onerror = () => console.warn(log, "the Saves control could not be loaded from", url);
    document.head.appendChild(script);
  }

  // arcade.js likewise: ready() runs once it has loaded or failed to (the
  // queue is then flushed or dropped).
  function loadScript(url, ready, log) {
    if (window.TakArcade) { ready(); return; }
    const script = document.createElement("script");
    script.src = url;
    script.onload = ready;
    script.onerror = () => { console.warn(log, "scores could not be loaded from", url); ready(); };
    document.head.appendChild(script);
  }

  return { start: start };
})();
