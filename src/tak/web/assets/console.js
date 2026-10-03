// The page side of tak's console runtime: a terminal for an ordinary
// input()/print() program running under Pyodide in console-worker.js.
//
// A game's index.html loads console.css and this file, then calls
//
//   TakConsole.start({
//     bundleUrl: '/web/game.zip',     // built by tak.web.bundle
//     entry: 'main.py',               // the script to run, inside the bundle
//     idbName: 'mygame-files',        // where the files it writes are kept
//     elementId: 'console',           // optional; the element to draw into
//   });
//
// Under the terminal goes a "Saves" control (saves.js, loaded from
// config.savesUrl, default /tak/saves.js): download the kept files as one
// save file, and load them from one.
//
// The terminal is a scrolling output pane and one real text field. A real
// <input> (rather than key events on a canvas) is what makes a phone's
// on-screen keyboard open, autocorrect stay out of the way, and paste work.

window.TakConsole = (function () {
  const RING_SIZE = 16384;
  const IDB_STORE = "files";
  const IDB_VERSION = 1;
  // Escape sequences console programs print. A "clear screen" (ESC[2J, or
  // ESC c) clears the pane; every other sequence (colours, cursor moves) is
  // dropped rather than shown as garbage.
  const CLEAR_SEQUENCE = /\x1b\[[0-9;]*[2-3]J|\x1bc/;
  const ANY_SEQUENCE = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b[@-Z\\-_]/g;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function start(config) {
    config = config || {};
    const log = config.logPrefix || "[tak-console]";
    const host = document.getElementById(config.elementId || "console") || document.body;

    const root = element("div", "tak-console");
    const screen = element("pre", "tak-console-screen");
    screen.setAttribute("role", "log");
    screen.setAttribute("aria-live", "polite");
    const status = element("div", "tak-console-status", "Starting…");
    const form = element("form", "tak-console-line");
    const prompt = element("span", "tak-console-caret", ">");
    prompt.setAttribute("aria-hidden", "true");
    const input = element("input", "tak-console-input");
    input.type = "text";
    input.setAttribute("aria-label", "Your input");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("enterkeyhint", "send");
    input.disabled = true;
    const send = element("button", "tak-console-send", "Enter");
    send.type = "submit";
    send.disabled = true;
    form.append(prompt, input, send);
    root.append(status, screen, form);
    host.appendChild(root);

    function setStatus(message, isError) {
      status.textContent = message || "";
      status.style.display = message ? "" : "none";
      status.classList.toggle("error", !!isError);
    }

    function scrollToEnd() {
      screen.scrollTop = screen.scrollHeight;
    }

    function write(text, className) {
      if (!text) return;
      let rest = text;
      let match;
      while ((match = CLEAR_SEQUENCE.exec(rest))) {
        rest = rest.slice(match.index + match[0].length);
        screen.textContent = "";
      }
      rest = rest.replace(ANY_SEQUENCE, "");
      if (!rest) return;
      if (className) screen.appendChild(element("span", className, rest));
      else {
        const last = screen.lastChild;
        if (last && last.nodeType === Node.TEXT_NODE) last.appendData(rest);
        else screen.appendChild(document.createTextNode(rest));
      }
      scrollToEnd();
    }

    function setWaiting(waiting) {
      input.disabled = !waiting;
      send.disabled = !waiting;
      root.classList.toggle("waiting", waiting);
      if (waiting && !config.noAutofocus) {
        // preventScroll: on a phone, focusing must not jump the page around.
        try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
      }
    }

    // -- Save export / import (saves.js) -------------------------------------
    // Once a save file is being loaded, this page keeps no files of its own
    // accord (keepFiles checks the flag when its transaction is created) and
    // the Worker is terminated, so nothing can erase what the import writes
    // before the page reloads. See saves.js for the whole ordering.
    const idbName = config.idbName || "tak-console-files";
    let savesFrozen = false;
    let worker = null;
    function stopForSaveTransfer() {
      savesFrozen = true;
      if (worker) worker.terminate();
      setWaiting(false);
      setStatus("The game was stopped to load your saves.", false);
    }
    // Before anything that can refuse to start, so files kept earlier can
    // still be downloaded from a browser that cannot run the game.
    loadSaveTransfer(config.savesUrl || "/tak/saves.js", function () {
      window.TakSaves.attach({
        idbName: idbName,
        root: "/game",
        after: root,
        stop: stopForSaveTransfer,
        log: log,
      });
    }, log);

    if (typeof SharedArrayBuffer === "undefined") {
      setStatus(
        "This browser can't run the game here: SharedArrayBuffer is unavailable, which " +
        "usually means the page wasn't served with the Cross-Origin-Opener-Policy and " +
        "Cross-Origin-Embedder-Policy headers. Open it from its own site over https.",
        true
      );
      return;
    }

    const sab = new SharedArrayBuffer(8 + RING_SIZE);
    const meta = new Int32Array(sab, 0, 2);
    const ring = new Uint8Array(sab, 8, RING_SIZE);

    function sendLine(text) {
      const bytes = new TextEncoder().encode(text.replace(/\r?\n/g, " ") + "\n");
      const writeIndex = Atomics.load(meta, 0);
      const readIndex = Atomics.load(meta, 1);
      if (bytes.length > RING_SIZE - (writeIndex - readIndex)) {
        console.warn(log, "input dropped: the ring buffer is full");
        return;
      }
      for (let i = 0; i < bytes.length; i++) ring[(writeIndex + i) % RING_SIZE] = bytes[i];
      Atomics.store(meta, 0, writeIndex + bytes.length);
      Atomics.notify(meta, 0);
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (input.disabled) return;
      const line = input.value;
      input.value = "";
      write(line + "\n", "tak-console-echo");
      setWaiting(false);
      sendLine(line);
    });
    // Tapping anywhere on the terminal brings the keyboard back.
    screen.addEventListener("click", () => {
      if (!input.disabled && !window.getSelection().toString()) input.focus();
    });

    // -- Kept files: IndexedDB is written here, never in the blocked Worker --
    function keepFiles(files, deleted) {
      if (savesFrozen) return;
      const request = indexedDB.open(idbName, IDB_VERSION);
      request.onupgradeneeded = (ev) => {
        const db = ev.target.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
      };
      request.onsuccess = (ev) => {
        const db = ev.target.result;
        // Checked again where the transaction is created: a save file may
        // have started loading while the database was opening.
        if (savesFrozen) { db.close(); return; }
        let tx;
        try { tx = db.transaction(IDB_STORE, "readwrite"); }
        catch (e) { console.warn(log, "could not keep files:", e); db.close(); return; }
        tx.oncomplete = () => db.close();
        tx.onerror = () => { console.warn(log, "keeping files failed:", tx.error); db.close(); };
        tx.onabort = () => { console.warn(log, "files not kept, stored files unchanged:", tx.error); db.close(); };
        // Never cleared, all or nothing: see mirrorToStore.
        mirrorToStore(tx.objectStore(IDB_STORE), files, deleted, log);
      };
      request.onerror = () => console.warn(log, "could not open file storage:", request.error);
    }

    worker = new Worker(config.workerUrl || "/tak/console-worker.js");
    worker.onmessage = (event) => {
      const message = event.data;
      switch (message.type) {
        case "status": setStatus(message.msg); break;
        case "out": write(message.text); break;
        case "err": write(message.text, "tak-console-stderr"); break;
        case "clear": screen.textContent = ""; break;
        case "waiting": setStatus(""); setWaiting(true); break;
        case "files": keepFiles(message.files, message.deleted); break;
        case "nosave": write(message.msg + "\n", "tak-console-note"); break;
        case "exit":
          setWaiting(false);
          write("\n[The game has ended. Reload the page to play again.]\n", "tak-console-note");
          form.style.display = "none";
          break;
        case "error":
          setWaiting(false);
          setStatus("The game stopped: " + message.msg + " — reload the page to start again.", true);
          break;
      }
    };
    worker.onerror = (event) => {
      setStatus("The game could not be started: " + (event.message || "unknown error") +
                " — reload the page to try again.", true);
    };
    worker.postMessage({
      type: "init",
      sab: sab,
      ringSize: RING_SIZE,
      config: {
        bundleUrl: config.bundleUrl || "/web/game.zip",
        entry: config.entry || "main.py",
        idbName: idbName,
        packages: config.packages || [],
        pyodideUrl: config.pyodideUrl,
        logPrefix: log,
      },
    });
  }

  // saves.js is shared with tak's own front-end (boot.js) and fetched on
  // demand, so a game's page does not have to list it. If it cannot be
  // loaded the game runs as before, without the Saves control.
  // One sync, applied inside keepFiles' readwrite transaction. Every file
  // sent is written; a stored file not sent is deleted only if `deleted`
  // names it (console-worker.js names only files this session restored or
  // kept and the program then removed). Everything else in the store is kept.
  // Identical to boot.js's: the store used to be cleared on every sync, which
  // erased any kept file missing from the Worker's copy.
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

  return { start: start };
})();
