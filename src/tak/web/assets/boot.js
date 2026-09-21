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
    const idbName = config.idbName || "tak-saves";

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

    // The file map is the whole save directory, so the store is cleared first
    // - that is what makes deleting a save slot in-game actually stick.
    function idbWrite(files) {
      idbOpen().then((db) => {
        let tx;
        try { tx = db.transaction(IDB_STORE, "readwrite"); }
        catch (e) { console.warn(log, "could not save to IndexedDB:", e); db.close(); return; }
        try { tx.objectStore(IDB_STORE).clear(); } catch {}
        for (const [path, content] of Object.entries(files)) {
          try { tx.objectStore(IDB_STORE).put(content, path); } catch (e) {
            console.warn(log, "could not save", path, e);
          }
        }
        tx.oncomplete = () => db.close();
        tx.onerror    = () => { console.warn(log, "save failed:", tx.error); db.close(); };
      }).catch((err) => console.warn(log, "could not open save storage:", err));
    }

    // -- Worker ----------------------------------------------------------------
    const worker = new Worker(config.workerUrl || "/tak/game-worker.js");
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
      if (message.type === "save")   { idbWrite(message.files); return; }
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
        saveDir: config.saveDir || "/saves",
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

  return { start: start };
})();
