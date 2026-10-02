// Web Worker: runs an ordinary console program - plain input() and print(),
// no tak UI - in the player's browser under Pyodide. The other half is
// console.js, which draws the terminal.
//
//   main -> worker:  { type: 'init', sab: SharedArrayBuffer, ringSize, config }
//   worker -> main:  { type: 'status', msg }       boot progress
//                    { type: 'out', text }         program output, as written
//                    { type: 'err', text }         stderr (tracebacks)
//                    { type: 'clear' }             the program cleared the screen
//                    { type: 'waiting' }           the program is reading a line
//                    { type: 'files', files }      every file to persist (see below)
//                    { type: 'nosave', msg }       kept files could not be read, so
//                                                  nothing will be written this session
//                    { type: 'exit', code }        the program finished
//                    { type: 'error', msg }        the runtime itself failed
//
// config (set by the page through TakConsole.start):
//   bundleUrl   game.zip, built by tak.web.bundle     e.g. '/web/game.zip'
//   entry       the script to run as __main__, inside the bundle  e.g. 'main.py'
//   idbName     the IndexedDB database files are kept in
//   packages    Pyodide packages to load first
//   pyodideUrl  the pyodide.js to load
//   logPrefix   for the browser console
//
// -- Input: a blocking stdin over shared memory -------------------------------
// input() is synchronous, so the Worker blocks while the program waits for a
// line - and a blocked Worker never runs onmessage. The main thread instead
// writes each line into a SharedArrayBuffer ring (sabMeta = [write, read]),
// and stdin waits on it with Atomics.wait, which needs no event loop. This is
// the same transport tak's own front-end uses (boot.js / game-worker.js).
//
// -- Files: whatever the program writes is kept --------------------------------
// A console game saves by writing files next to itself (save.txt, scores.json,
// a saves/ folder). The bundle is unpacked to /game and the program runs with
// /game as its working directory; every file under /game that the program
// created or changed is mirrored to IndexedDB and written back over the fresh
// bundle on the next visit. Like game-worker.js, the Worker only collects the
// files (synchronously, at each input prompt and at exit) and posts them; the
// main thread does the IndexedDB writes from its own, unblocked, event loop.

const DEFAULT_PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v0.29.5/full/pyodide.js';
const GAME_DIRECTORY = '/game';
const IDB_STORE = 'files';
const IDB_VERSION = 1;

function idbOpen(name) {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(name, IDB_VERSION);
        req.onupgradeneeded = (e) => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
        };
        req.onsuccess = (e) => resolve(e.target.result);
        req.onerror = (e) => reject(e.target.error);
    });
}

// Before Python starts (so IndexedDB callbacks still fire): write every kept
// file back over the unpacked bundle. Never rejects - a browser that will not
// hand stored data back starts the player fresh rather than not at all - but
// it reports whether the read succeeded, because what happens next depends on
// it: the page REPLACES the stored files with each sync, so syncing after a
// failed read would replace the player's saves with nothing.
//
// Resolves to { ok, paths }: ok is false if the store could not be read, and
// paths is every file that was written back.
async function restoreFiles(pyodide, idbName, log) {
    const paths = new Set();
    try {
        const db = await Promise.race([
            idbOpen(idbName),
            new Promise((_, reject) => setTimeout(() => reject(new Error('IndexedDB open timed out')), 5000)),
        ]);
        const entries = await new Promise((resolve, reject) => {
            const found = [];
            const tx = db.transaction(IDB_STORE, 'readonly');
            const cursor = tx.objectStore(IDB_STORE).openCursor();
            cursor.onsuccess = (ev) => {
                const c = ev.target.result;
                if (c) { found.push([c.key, c.value]); c.continue(); } else resolve(found);
            };
            cursor.onerror = () => reject(cursor.error);
        });
        for (const [path, content] of entries) {
            if (typeof path !== 'string' || !path.startsWith(GAME_DIRECTORY + '/') || path.includes('/../')) continue;
            const parts = path.split('/').filter(Boolean);
            let directory = '';
            for (let i = 0; i < parts.length - 1; i++) {
                directory += '/' + parts[i];
                try { pyodide.FS.mkdir(directory); } catch {}
            }
            try {
                pyodide.FS.writeFile(path, content);
                paths.add(path);
            } catch (e) {
                // A file that cannot be written back cannot be kept either:
                // treat the whole restore as failed rather than drop it.
                console.warn(log, 'could not restore', path, e);
                db.close();
                return { ok: false, paths };
            }
        }
        if (entries.length) console.log(log, `${entries.length} kept file(s) restored`);
        db.close();
        return { ok: true, paths };
    } catch (err) {
        console.warn(log, 'could not read kept files:', err);
        return { ok: false, paths };
    }
}

function walkFiles(pyodide, root, visit) {
    let names;
    try { names = pyodide.FS.readdir(root); } catch { return; }
    for (const name of names) {
        if (name === '.' || name === '..' || name === '__pycache__') continue;
        const path = `${root}/${name}`;
        let stat;
        try { stat = pyodide.FS.stat(path); } catch { continue; }
        if ((stat.mode & 0o170000) === 0o040000) walkFiles(pyodide, path, visit);
        else visit(path, stat);
    }
}

// A file is kept if it was restored from storage (it is the player's, whatever
// its timestamp says - a restore can land in the same millisecond as the
// unpack), if it was not in the bundle as unpacked, or if it has been written
// since (its mtime moved). Bundle files the program never touched are not
// kept: a new deploy of the game must win over a stale copy. A kept file the
// program deletes is gone from the walk, so the deletion sticks.
function makeFileSync(pyodide, pristine, restored, log) {
    let lastSignature = null;
    return () => {
        const changed = [];
        walkFiles(pyodide, GAME_DIRECTORY, (path, stat) => {
            const mtime = stat.mtime instanceof Date ? stat.mtime.getTime() : Number(stat.mtime);
            const original = pristine.get(path);
            if (restored.has(path) || original === undefined || original !== mtime) {
                changed.push([path, mtime, stat.size]);
            }
        });
        const signature = JSON.stringify(changed);
        if (signature === lastSignature) return;  // nothing new since the last prompt
        lastSignature = signature;
        const files = {};
        for (const [path] of changed) {
            try { files[path] = pyodide.FS.readFile(path); } catch (e) { console.warn(log, 'could not read', path, e); }
        }
        self.postMessage({ type: 'files', files });
    };
}

self.onmessage = async (event) => {
    if (event.data.type !== 'init') return;
    const { sab, ringSize } = event.data;
    const config = event.data.config || {};
    const log = config.logPrefix || '[tak-console]';
    const meta = new Int32Array(sab, 0, 2);
    const data = new Uint8Array(sab, 8, ringSize);
    const decoder = new TextDecoder();
    const outDecoder = new TextDecoder();
    const errDecoder = new TextDecoder();
    let pending = '';  // bytes already read from the ring but not yet returned
    let syncFiles = () => {};

    // One line from the ring, blocking until the player sends one.
    function readLine() {
        syncFiles();
        self.postMessage({ type: 'waiting' });
        for (;;) {
            const newline = pending.indexOf('\n');
            if (newline >= 0) {
                const line = pending.slice(0, newline + 1);
                pending = pending.slice(newline + 1);
                return line;
            }
            const write = Atomics.load(meta, 0);
            const read = Atomics.load(meta, 1);
            if (write === read) {
                Atomics.wait(meta, 0, write);
                continue;
            }
            const bytes = new Uint8Array(write - read);
            for (let i = 0; i < bytes.length; i++) bytes[i] = data[(read + i) % ringSize];
            Atomics.store(meta, 1, write);
            pending += decoder.decode(bytes, { stream: true });
        }
    }

    try {
        self.postMessage({ type: 'status', msg: 'Loading the Python runtime…' });
        importScripts(config.pyodideUrl || DEFAULT_PYODIDE_URL);
        const pyodide = await loadPyodide();

        // Raw writes, so a prompt with no newline (input("Name: ")) shows at once.
        pyodide.setStdout({ write: (buffer) => {
            self.postMessage({ type: 'out', text: outDecoder.decode(buffer, { stream: true }) });
            return buffer.length;
        }});
        pyodide.setStderr({ write: (buffer) => {
            self.postMessage({ type: 'err', text: errDecoder.decode(buffer, { stream: true }) });
            return buffer.length;
        }});
        pyodide.setStdin({ stdin: readLine, isatty: false });

        if (config.packages && config.packages.length) {
            self.postMessage({ type: 'status', msg: 'Installing Python packages…' });
            await pyodide.loadPackage(config.packages);
        }

        self.postMessage({ type: 'status', msg: 'Downloading the game…' });
        const bundleUrl = config.bundleUrl || '/web/game.zip';
        const response = await fetch(bundleUrl);
        if (!response.ok) throw new Error(`${bundleUrl} fetch failed: ${response.status}`);
        pyodide.FS.mkdir(GAME_DIRECTORY);
        pyodide.unpackArchive(new Uint8Array(await response.arrayBuffer()), 'zip', { extractDir: GAME_DIRECTORY });

        const pristine = new Map();
        walkFiles(pyodide, GAME_DIRECTORY, (path, stat) => {
            pristine.set(path, stat.mtime instanceof Date ? stat.mtime.getTime() : Number(stat.mtime));
        });
        const restore = await restoreFiles(pyodide, config.idbName || 'tak-console-files', log);
        if (restore.ok) {
            syncFiles = makeFileSync(pyodide, pristine, restore.paths, log);
        } else {
            // Never sync after a failed read: the page replaces the store with
            // each sync, which would erase saves this session never saw. The
            // game still runs; this session's progress just is not kept.
            self.postMessage({ type: 'nosave', msg:
                "Your saved files could not be read, so this session's progress won't be saved " +
                '(your earlier saves are untouched). Reloading the page usually fixes this.' });
        }
        globalThis.takConsoleClear = () => self.postMessage({ type: 'clear' });
        // time.sleep() of our own. Pyodide picks its sleep strategy from the
        // browser it thinks it is in, and with a Safari user agent that path
        // tries to suspend the WebAssembly stack and dies with "SuspendError:
        // trying to suspend JS frames" (reproduced: any program that sleeps,
        // e.g. Kreatures between ticks). This runtime already requires
        // SharedArrayBuffer, so Atomics.wait on a private cell is a real,
        // blocking sleep in every browser it supports.
        const sleepCell = new Int32Array(new SharedArrayBuffer(4));
        globalThis.takConsoleSleep = (seconds) => {
            const ms = Number(seconds) * 1000;
            if (ms > 0) Atomics.wait(sleepCell, 0, 0, ms);
        };

        self.postMessage({ type: 'status', msg: '' });
        const entry = config.entry || 'main.py';
        const code = await pyodide.runPythonAsync(`
import os, runpy, sys, traceback
from js import takConsoleClear, takConsoleSleep
import time as _time

def _sleep(seconds):
    seconds = float(seconds)
    if seconds < 0:
        raise ValueError("sleep length must be non-negative")
    takConsoleSleep(seconds)

_time.sleep = _sleep

_entry = os.path.join(${JSON.stringify(GAME_DIRECTORY)}, ${JSON.stringify(entry)})
os.chdir(os.path.dirname(_entry))
sys.path[:0] = [os.path.dirname(_entry), ${JSON.stringify(GAME_DIRECTORY + '/src')}]
sys.argv = [${JSON.stringify(entry)}]

def _system(command):
    # There is no shell in a browser tab. The one thing console games ask a
    # shell for is to clear the screen, so that is honoured; anything else
    # fails the way a missing command would.
    if str(command).strip().lower() in ("cls", "clear"):
        takConsoleClear()
        return 0
    return 127

os.system = _system

_code = 0
try:
    runpy.run_path(_entry, run_name="__main__")
except SystemExit as _exit:
    _code = _exit.code if isinstance(_exit.code, int) else (0 if _exit.code is None else 1)
    if _exit.code is not None and not isinstance(_exit.code, int):
        print(_exit.code, file=sys.stderr)
except EOFError:
    _code = 0
except BaseException:
    traceback.print_exc()
    _code = 1
finally:
    sys.stdout.flush()
    sys.stderr.flush()
_code
`);
        syncFiles();
        self.postMessage({ type: 'exit', code: typeof code === 'number' ? code : 0 });
    } catch (err) {
        self.postMessage({ type: 'error', msg: String(err) });
    }
};
