// Web Worker: loads Pyodide, unpacks the game bundle, and runs a tak game's
// Python game loop in the player's own browser.
//
// Communication with the main thread (see boot.js):
//   main -> worker:  { type: 'init', sab: SharedArrayBuffer, ringSize, config }
//   worker -> main:  { type: 'status', msg: string }
//                    { type: 'ready' }
//                    { type: 'error', msg: string }
//                    { type: 'save', files: { path: content, ... } }
//                    string - a JSON {"type":"screen","screen":{...}} frame
//                             posted by PyodideUserInterface
//
// config (all set by the game's page through TakBoot.start):
//   bundleUrl   where game.zip is served            e.g. '/web/game.zip'
//   entry       the Python file to exec once unpacked, inside /game
//                                                    e.g. 'web/pyodide_main.py'
//   saveDir     the in-Worker save directory         e.g. '/saves'
//   saveDirEnv  the environment variable the game reads its save directory
//               from, set to saveDir before the entry point runs
//   idbName     the IndexedDB database saves are mirrored to
//   packages    Pyodide packages to load before the game ('jsonschema' is
//               always included: tak's save validation needs it)
//   pyodideUrl  the pyodide.js to load
//   messages    { runtime, restore, packages, download, start } status lines
//   logPrefix   for console output
//
// The player's responses arrive the other way, through the SharedArrayBuffer
// ring buffer the main thread writes into. They cannot come over postMessage:
// the game loop is synchronous, so it blocks the Worker while waiting for
// input, and a blocked Worker never runs onmessage.
//
// -- Why IndexedDB writes live on the main thread ------------------------------
// Pyodide's build uses Atomics.wait() for time.sleep() when SharedArrayBuffer
// is available, which blocks the Worker's JS event loop entirely - so IDB
// callbacks (macrotasks) can never fire while Python is running. Instead the
// Worker walks the save directory synchronously and postMessages the file map
// to the main thread, which writes to IDB from its own, unblocked, event loop.
// The initial restore still happens here in the Worker because it runs before
// Python starts, when nothing is blocking the event loop yet.

const DEFAULT_PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v0.29.5/full/pyodide.js';
const IDB_STORE   = 'files';
const IDB_VERSION = 1;

function idbOpen(name) {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(name, IDB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(IDB_STORE)) {
                db.createObjectStore(IDB_STORE);
            }
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror   = (e) => reject(e.target.error);
    });
}

// -- Save restore: read IndexedDB into the save directory before Python starts -
// Always resolves, never rejects: a browser that won't hand back stored data
// should start the player on a fresh save file, not refuse to load the game.
async function loadSavesFromIDB(pyodide, idbName, log) {
    try {
        const db = await Promise.race([
            idbOpen(idbName),
            new Promise((_, rej) =>
                setTimeout(() => rej(new Error('IndexedDB open timed out')), 5000)
            ),
        ]);

        const entries = await new Promise((resolve, reject) => {
            const result = [];
            let tx, cursorReq;
            try {
                tx        = db.transaction(IDB_STORE, 'readonly');
                cursorReq = tx.objectStore(IDB_STORE).openCursor();
            } catch (e) { reject(e); return; }
            cursorReq.onsuccess = (ev) => {
                const c = ev.target.result;
                if (c) { result.push({ path: c.key, content: c.value }); c.continue(); }
                else   resolve(result);
            };
            cursorReq.onerror = () => reject(cursorReq.error);
            tx.onerror        = () => reject(tx.error);
            tx.onabort        = () => reject(new Error('IndexedDB transaction aborted'));
        });

        let restored = 0;
        for (const { path, content } of entries) {
            // Save slots are directories (/saves/slot_1/save.json), so the
            // parents have to exist before the file can be written.
            const parts = path.split('/').filter(Boolean);
            let dir = '';
            for (let i = 0; i < parts.length - 1; i++) {
                dir += '/' + parts[i];
                try { pyodide.FS.mkdir(dir); } catch {}  // already there: fine
            }
            try {
                pyodide.FS.writeFile(path, content, { encoding: 'utf8' });
                restored++;
            } catch (e) {
                console.warn(log, 'could not restore save file', path, e);
            }
        }
        if (restored > 0) console.log(log, `${restored} save file(s) restored`);
        try { db.close(); } catch {}

    } catch (err) {
        console.warn(log, 'save restore skipped (starting fresh):', err);
    }
}

// -- Save flush: collect the save directory and hand it to the main thread -----
// Installed as globalThis.syncSaves, which tak.saves.browser calls after every
// write or delete. Walking pyodide.FS is pure JavaScript and needs no event
// loop, and postMessage is synchronous from the Worker's side - so this works
// even though Python is mid-call and the Worker is otherwise blocked.

function makeSyncSaves(pyodide, saveDirectory, log) {
    return () => {
        const files = {};
        function walk(path) {
            let entries;
            try { entries = pyodide.FS.readdir(path); } catch { return; }
            for (const name of entries) {
                if (name === '.' || name === '..') continue;
                const full = `${path}/${name}`;
                let stat;
                try { stat = pyodide.FS.stat(full); } catch { continue; }
                const isDirectory = (stat.mode & 0o170000) === 0o040000;
                if (isDirectory) {
                    walk(full);
                } else {
                    // Saves are JSON, so UTF-8 is the whole story here.
                    try {
                        files[full] = pyodide.FS.readFile(full, { encoding: 'utf8' });
                    } catch (e) {
                        console.warn(log, 'could not read save file', full, e);
                    }
                }
            }
        }
        walk(saveDirectory);
        self.postMessage({ type: 'save', files });
    };
}

// -- Worker entry point --------------------------------------------------------

self.onmessage = async (e) => {
    if (e.data.type !== 'init') return;

    const { sab, ringSize } = e.data;
    const config   = e.data.config || {};
    const log      = config.logPrefix || '[tak]';
    const messages = Object.assign({
        runtime:  'Loading Python runtime…',
        restore:  'Restoring saved games…',
        packages: 'Installing Python packages…',
        download: 'Downloading the game…',
        start:    'Starting…',
    }, config.messages || {});
    const saveDirectory = config.saveDir || '/saves';

    // [0] = write index, [1] = read index, both monotonically increasing and
    // taken modulo the ring size when indexing into the data region.
    globalThis.sabMeta     = new Int32Array(sab, 0, 2);
    globalThis.sabData     = new Uint8Array(sab, 8, ringSize);
    globalThis.sabRingSize = ringSize;
    globalThis.sendToMain  = (data) => self.postMessage(data);

    try {
        importScripts(config.pyodideUrl || DEFAULT_PYODIDE_URL);

        self.postMessage({ type: 'status', msg: messages.runtime });

        const pyodide = await loadPyodide({
            stdout: (msg) => console.log(log, msg),
            stderr: (msg) => console.warn(log, msg),
        });

        self.postMessage({ type: 'status', msg: messages.restore });

        pyodide.FS.mkdir(saveDirectory);
        await loadSavesFromIDB(pyodide, config.idbName || 'tak-saves', log);  // before Python: IDB callbacks still fire
        globalThis.syncSaves = makeSyncSaves(pyodide, saveDirectory, log);

        self.postMessage({ type: 'status', msg: messages.packages });

        // jsonschema is a real runtime dependency: tak's save readers validate
        // against the game's schemas on every load.
        const packages = Array.from(new Set(['jsonschema'].concat(config.packages || [])));
        await pyodide.loadPackage(packages);

        self.postMessage({ type: 'status', msg: messages.download });

        const bundleUrl = config.bundleUrl || '/web/game.zip';
        const resp = await fetch(bundleUrl);
        if (!resp.ok) throw new Error(`${bundleUrl} fetch failed: ${resp.status}`);
        const buf = await resp.arrayBuffer();
        pyodide.FS.mkdir('/game');
        pyodide.unpackArchive(new Uint8Array(buf), 'zip', { extractDir: '/game' });

        self.postMessage({ type: 'status', msg: messages.start });
        self.postMessage({ type: 'ready' });

        // chdir to /game so cwd-relative paths in the game (its schemas/)
        // resolve; the save-directory variable points the game's config at
        // the IndexedDB-backed directory. The bundle carries the tak package
        // under src/, so one sys.path entry covers both.
        const entry      = config.entry || 'web/pyodide_main.py';
        const saveDirEnv = config.saveDirEnv || 'TAK_SAVE_DIR';
        await pyodide.runPythonAsync(`
import os, sys
sys.path.insert(0, '/game/src')
os.chdir('/game')
os.environ[${JSON.stringify(saveDirEnv)}] = ${JSON.stringify(saveDirectory)}
exec(compile(open(${JSON.stringify('/game/' + entry)}).read(), ${JSON.stringify(entry)}, 'exec'))
`);

    } catch (err) {
        self.postMessage({ type: 'error', msg: String(err) });
    }
};
