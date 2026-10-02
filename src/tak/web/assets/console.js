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
    const idbName = config.idbName || "tak-console-files";
    function keepFiles(files) {
      const request = indexedDB.open(idbName, IDB_VERSION);
      request.onupgradeneeded = (ev) => {
        const db = ev.target.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
      };
      request.onsuccess = (ev) => {
        const db = ev.target.result;
        let tx;
        try { tx = db.transaction(IDB_STORE, "readwrite"); }
        catch (e) { console.warn(log, "could not keep files:", e); db.close(); return; }
        const store = tx.objectStore(IDB_STORE);
        // The map is every file the program has written, so replacing the
        // store's contents is what makes a deleted save stay deleted.
        store.clear();
        for (const [path, content] of Object.entries(files)) store.put(content, path);
        tx.oncomplete = () => db.close();
        tx.onerror = () => { console.warn(log, "keeping files failed:", tx.error); db.close(); };
      };
      request.onerror = () => console.warn(log, "could not open file storage:", request.error);
    }

    const worker = new Worker(config.workerUrl || "/tak/console-worker.js");
    worker.onmessage = (event) => {
      const message = event.data;
      switch (message.type) {
        case "status": setStatus(message.msg); break;
        case "out": write(message.text); break;
        case "err": write(message.text, "tak-console-stderr"); break;
        case "clear": screen.textContent = ""; break;
        case "waiting": setStatus(""); setWaiting(true); break;
        case "files": keepFiles(message.files); break;
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

  return { start: start };
})();
