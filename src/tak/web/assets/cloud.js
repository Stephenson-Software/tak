// Cloud saves for tak games on arcade (Stephenson-Software RFC 0016): a
// signed-in player can back a game's saves up to their arcade-social account
// and, where the game allows it, have newer saves from their other devices
// brought down automatically.
//
// boot.js loads this only when the page config has `cloudSaves: true` and the
// page is a game on arcade (https://<slug>.play.danielstephenson.dev). It
// never runs anywhere else, and a player who has not turned it on sends
// nothing: no request to /v1/saves is made until the player enrolls.
//
// -- What can never happen here -----------------------------------------------
//
//   * The game never waits for the cloud to save. IndexedDB is written first,
//     exactly as without this file; uploads trail it.
//   * Nothing is ever newest-wins. The server refuses an upload that is not
//     based on its current version (409); this page then merges per unit, and
//     a unit changed on two devices is KEPT TWICE (the second copy goes into
//     the next free slot_N).
//   * An error is never read as "nothing in the cloud". Any failure - network,
//     sign-in, 5xx, the kill switch - is "unknown", and nothing is written or
//     uploaded because of it.
//   * A pull is an import (saves.js): validated in full, the store backed up
//     into <idbName>.tak-backups and read back first, then put-only (it never
//     deletes a local file, and never sends the runtime's `deleted` list),
//     then read back, then the page reloads.
//   * A unit missing from this browser that the game did not delete (a
//     session that lost track of a slot) is carried forward from the last
//     version, never dropped; the server refuses a dropped unit anyway (422).
//   * Nothing is uploaded from a session whose restore failed (tak's nosave).
//   * A deletion is never sent to another device: a slot deleted elsewhere
//     stays here until the player deletes it here (RFC 0016 §4.5).
//
// -- The algorithm -------------------------------------------------------------
//
// Per browser (localStorage, per origin and store):
//   sync             {id, units: {name: sha}}: the version this store equalled
//                    when it last uploaded or pulled
//   pendingDeleted   units the game deleted that the cloud has not heard of
//   deletedElsewhere {name: sha}: local units the cloud no longer has; left
//                    out of uploads while unchanged here
//   missing          {name: sha}: units this store lost without the game
//                    deleting them, carried forward from the cloud. If one
//                    comes back with different content (a new game started in
//                    a slot that looked empty), it is a conflict and BOTH are
//                    kept - it never replaces the lost save in the cloud.
//
// On load, before the Worker starts (3 s budget):
//   head == sync:  upload local changes if there are any (parent = head);
//   otherwise:     merge(base = sync's version, head, local) per unit, upload
//                  the merge if it differs from head, then - if the game pulls
//                  (Stage 2) - pull the units that differ here, and reload.
//                  A game that does not pull (Stage 1) offers "Load" instead.
// After each committed save (at most one upload per 30 s): upload local
// changes; on 409 merge and upload the merge, and pause until the next load,
// which brings this store up to date before the game starts.
//
// tests/savesclient.py in Stephenson-Software/arcade-social is a Python copy
// of this algorithm that its randomized three-device test runs against the
// server's real rules; tests/web/test_cloud_saves.py runs this file's own
// engine through the same kind of histories under Node.

window.TakCloud = (function () {
  "use strict";

  const API = "https://api.play.danielstephenson.dev";
  const GAME_HOST = /^[a-z][a-z0-9-]{1,30}\.play\.danielstephenson\.dev$/;
  const SLOT = /^slot_([0-9]+)$/;
  const MAX_SLOTS = 99;
  const UPLOAD_SPACING_MS = 30 * 1000;
  const PRESTART_BUDGET_MS = 3000;
  const REQUEST_TIMEOUT_MS = 10000;
  const STATE_PREFIX = "tak-cloud:";

  // -- Pure helpers (no DOM, no storage, no network) ---------------------------

  // Compare strings by Unicode code point, as Python sorts them.
  function codePointCompare(a, b) {
    const x = Array.from(a), y = Array.from(b);
    const n = Math.min(x.length, y.length);
    for (let i = 0; i < n; i++) {
      const d = x[i].codePointAt(0) - y[i].codePointAt(0);
      if (d) return d;
    }
    return x.length - y.length;
  }

  // A unit's canonical text: its {path: content} map as compact JSON with
  // sorted keys. arcade-social hashes exactly these bytes (UTF-8), so a
  // unit's sha here equals the one in the server's version listing.
  function canonical(unitFiles) {
    const keys = Object.keys(unitFiles).sort(codePointCompare);
    const parts = [];
    for (const key of keys) {
      const value = unitFiles[key];
      let encoded;
      if (typeof value === "string") encoded = JSON.stringify(value);
      else if (value && typeof value === "object" && typeof value.base64 === "string" && Object.keys(value).length === 1) {
        encoded = '{"base64":' + JSON.stringify(value.base64) + "}";
      } else {
        throw new Error("a saved file of a kind cloud saves cannot carry: " + key);
      }
      parts.push(JSON.stringify(key) + ":" + encoded);
    }
    return "{" + parts.join(",") + "}";
  }

  function hex(buffer) {
    return Array.from(new Uint8Array(buffer)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function sha256(text) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return hex(digest);
  }

  function unitName(path, root) {
    return path.slice(root.length + 1).split("/", 1)[0];
  }

  // {path: content} -> {unit: {path: content}}
  function unitsOf(files, root) {
    const units = {};
    for (const path of Object.keys(files)) {
      const name = unitName(path, root);
      (units[name] = units[name] || {})[path] = files[path];
    }
    return units;
  }

  function flatten(units) {
    const files = {};
    for (const name of Object.keys(units)) Object.assign(files, units[name]);
    return files;
  }

  function rename(unitFiles, root, from, to) {
    const prefix = root + "/" + from;
    const renamed = {};
    for (const path of Object.keys(unitFiles)) renamed[root + "/" + to + path.slice(prefix.length)] = unitFiles[path];
    return renamed;
  }

  async function shasOf(units) {
    const result = {};
    for (const name of Object.keys(units)) result[name] = await sha256(canonical(units[name]));
    return result;
  }

  // A unit's content without its name, so a copy kept under another slot is
  // recognised as the same save.
  function contentKey(unitFiles, root, name) {
    return sha256(canonical(rename(unitFiles, root, name, "_")));
  }

  class Unresolvable extends Error {}

  function sameShas(a, b) {
    const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
    if (ka.length !== kb.length) return false;
    for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i] || a[ka[i]] !== b[kb[i]]) return false;
    return true;
  }

  // Per-unit three-way merge (RFC 0016 §4.4). Each argument is
  // {name: unitFiles}. Returns {merged, kept}: kept lists the new slots that
  // hold this device's copy of a unit that changed on two devices. Throws
  // Unresolvable when such a copy cannot be given a new name (not a slot_N
  // unit, or all 99 slots in use) - nothing is written then.
  async function merge3(base, head, local, root) {
    const b = await shasOf(base), h = await shasOf(head), l = await shasOf(local);
    const names = Array.from(new Set(Object.keys(base).concat(Object.keys(head), Object.keys(local)))).sort();
    const merged = {};
    const conflicts = [];
    for (const name of names) {
      const bs = b[name], hs = h[name], ls = l[name];
      if (hs === ls) { if (hs !== undefined) merged[name] = head[name]; }
      else if (bs === hs) { if (ls !== undefined) merged[name] = local[name]; }   // only here changed (or deleted on purpose)
      else if (bs === ls) { if (hs !== undefined) merged[name] = head[name]; }    // only the cloud changed
      else if (hs === undefined) merged[name] = local[name];                      // a change beats a removal
      else if (ls === undefined) merged[name] = head[name];
      else { merged[name] = head[name]; conflicts.push(name); }                   // changed on both: keep both
    }
    const present = new Set();
    for (const name of Object.keys(merged)) present.add(await contentKey(merged[name], root, name));
    const taken = new Set(names.concat(Object.keys(merged)));
    const kept = [];
    for (const name of conflicts) {
      const key = await contentKey(local[name], root, name);
      if (present.has(key)) continue;   // that copy is already in the merged set
      if (!SLOT.test(name)) throw new Unresolvable(name);
      let free = null;
      for (let n = 1; n <= MAX_SLOTS; n++) if (!taken.has("slot_" + n)) { free = "slot_" + n; break; }
      if (!free) throw new Unresolvable(name);
      taken.add(free);
      merged[free] = rename(local[name], root, name, free);
      present.add(key);
      kept.push(free);
    }
    return { merged: merged, kept: kept };
  }

  // -- The engine (storage and network injected; Node runs it in tests) -----------
  //
  // options:
  //   store, root, device, label, pullEnabled
  //   api:   status() / upload(body) / version(id) -> Promise<{status, body}>
  //          (status 0 for a network failure or timeout)
  //   state: load() -> object|null, save(object)
  //   local: read() -> Promise<{path: content}> in the save-file encoding
  //          pull(files, expected) -> Promise: the import order, put-only;
  //          resolves once the files are stored and read back. It must refuse
  //          (reject, writing nothing) if the store no longer equals
  //          `expected`, the files this merge was computed from.
  //   format: "tak-saves"
  function createEngine(options) {
    const root = options.root;
    const api = options.api;
    let state = normalize(options.state.load());
    let paused = null;       // why uploads stop until the next load
    let disabled = null;     // a reason nothing may be uploaded this session
    let lastResult = null;   // for the panel: {outcome, at, detail}

    function normalize(saved) {
      const s = saved && typeof saved === "object" ? saved : {};
      return {
        sync: s.sync && typeof s.sync.id === "number" && s.sync.units ? s.sync : null,
        pendingDeleted: Array.isArray(s.pendingDeleted) ? s.pendingDeleted.filter((n) => typeof n === "string") : [],
        deletedElsewhere: s.deletedElsewhere && typeof s.deletedElsewhere === "object" ? s.deletedElsewhere : {},
        missing: s.missing && typeof s.missing === "object" ? s.missing : {},
      };
    }

    function persist() { options.state.save(state); }

    function result(outcome, detail) {
      lastResult = { outcome: outcome, at: new Date(), detail: detail || null };
      return outcome;
    }

    async function fetchUnits(id) {
      const response = await api.version(id);
      if (response.status !== 200 || !response.body || typeof response.body.files !== "object") return null;
      return unitsOf(response.body.files, root);
    }

    // The local units as the cloud should see them (see the top of this file).
    async function view(localUnits, baseUnits) {
      const result = {};
      for (const name of Object.keys(localUnits)) {
        const recorded = state.deletedElsewhere[name];
        if (recorded && recorded === await sha256(canonical(localUnits[name]))) continue;
        result[name] = localUnits[name];
      }
      if (state.sync) {
        for (const name of Object.keys(state.sync.units)) {
          if (name in localUnits || state.pendingDeleted.includes(name) || name in state.deletedElsewhere) continue;
          if (!baseUnits || !(name in baseUnits)) return null;
          result[name] = baseUnits[name];   // carried forward: never dropped implicitly
          if (state.missing[name] !== state.sync.units[name]) { state.missing[name] = state.sync.units[name]; persist(); }
        }
      }
      return result;
    }

    function needsBase(localUnits) {
      if (!state.sync) return false;
      return Object.keys(state.sync.units).some((name) =>
        !(name in localUnits) && !state.pendingDeleted.includes(name) && !(name in state.deletedElsewhere));
    }

    function put(parent, units, kind, headShas, extra) {
      const removed = Object.keys(headShas).filter((name) => !(name in units)).sort();
      const body = {
        parent: parent,
        device: options.device,
        deviceLabel: options.label,
        kind: kind,
        file: { format: options.format || "tak-saves", version: 1, game: options.store, exported: new Date().toISOString(), files: flatten(units) },
      };
      if (removed.length) body.confirmRemoved = removed;
      if (extra && extra.confirmShrink) body.confirmShrink = true;
      return api.upload(body);
    }

    async function synced(id, units) {
      state.sync = { id: id, units: await shasOf(units) };
      state.pendingDeleted = [];
      for (const name of Object.keys(units)) delete state.deletedElsewhere[name];   // the cloud has it again
      persist();
    }

    function refusal(response) {
      const code = response.body && response.body.saves;
      if (response.status === 409) return "retry";
      if (response.status === 422 && code === "shrink") { paused = "shrink"; return result("paused", "shrink"); }
      if (response.status === 422) { paused = "refused"; return result("paused", code || "refused"); }
      if (response.status === 413) { paused = "quota"; return result("paused", "quota"); }
      return result("failed", code || ("http-" + response.status));
    }

    // One pass. allowPull: this is the load-time sync and the Worker has not
    // started. beforePull(): called right before a pull writes; false means
    // the session started meanwhile, so do not pull now.
    async function syncOnce(allowPull, beforePull, extra) {
      // The store is read first, before any network: a unit lost from it is
      // recorded as missing even while the cloud cannot be reached, so that
      // whatever later takes its slot is kept beside it, never over it.
      let localFiles;
      try { localFiles = await options.local.read(); }
      catch (e) { return result("unknown", "local-unreadable"); }
      const localUnits = unitsOf(localFiles, root);
      recordMissing(localUnits);
      const statusResponse = await api.status();
      const info = statusResponse.body;
      if (statusResponse.status !== 200 || !info || info.enrolled !== true || !("head" in info)) {
        return result("unknown", statusResponse.status === 200 ? "not-enrolled" : "http-" + statusResponse.status);
      }
      const head = info.head;
      // A lost unit that is back as it was is no longer missing; one that is
      // back DIFFERENT must not be uploaded over the lost one (see `missing`).
      const reappeared = [];
      for (const name of Object.keys(state.missing)) {
        if (state.pendingDeleted.includes(name)) { delete state.missing[name]; persist(); continue; }
        if (!(name in localUnits)) continue;
        if (await sha256(canonical(localUnits[name])) === state.missing[name]) { delete state.missing[name]; persist(); }
        else reappeared.push(name);
      }

      if (head === null) {
        // Enrolled, nothing uploaded yet (or the cloud copy was deleted).
        const first = {};
        for (const name of Object.keys(localUnits)) {
          const recorded = state.deletedElsewhere[name];
          if (recorded && recorded === await sha256(canonical(localUnits[name]))) continue;
          first[name] = localUnits[name];
        }
        if (!Object.keys(first).length) return result("empty");
        if (!info.writable) return result("paused", info.reason || "read-only");
        const response = await put(null, first, "enroll", {}, extra);
        if (response.status === 200 || response.status === 201) {
          await synced(response.body.id, first);
          return result("uploaded");
        }
        return refusal(response);
      }

      const headShas = {};
      for (const unit of head.units || []) headShas[unit.name] = unit.sha256;

      if (state.sync && state.sync.id === head.id && !reappeared.length) {
        let baseUnits = {};
        if (needsBase(localUnits)) {
          baseUnits = await fetchUnits(state.sync.id);
          if (!baseUnits) return result("unknown", "base-unreadable");
        }
        const current = await view(localUnits, baseUnits);
        if (!current) return result("unknown", "base-incomplete");
        let outcome = "in-sync";
        if (!sameShas(await shasOf(current), state.sync.units)) {
          if (!info.writable) return result("paused", info.reason || "read-only");
          const response = await put(head.id, current, "upload", headShas, extra);
          if (response.status !== 200 && response.status !== 201) return refusal(response);
          await synced(response.body.id, current);
          outcome = "uploaded";
        }
        // Units the cloud has that this store lost without the game deleting
        // them (carried forward above) come back at load, before the game
        // starts. Put-only into paths that are absent: nothing is overwritten.
        const lost = {};
        for (const name of Object.keys(current)) if (!(name in localUnits)) lost[name] = current[name];
        if (allowPull && Object.keys(lost).length && options.pullEnabled && info.pull) {
          if (beforePull && beforePull() === false) return result(outcome);
          await options.local.pull(flatten(lost), localFiles);
          return result("pulled", "restored");
        }
        return result(outcome);
      }

      // The cloud moved on, or this browser has never synced: merge.
      let base = {};
      if (state.sync) {
        base = await fetchUnits(state.sync.id);
        if (!base) return result("unknown", "base-unreadable");
      }
      // Merge a reappeared unit as if this store had never seen the cloud's
      // copy: the cloud's stays under its name, this one goes to a free slot.
      const mergeBase = Object.assign({}, base);
      for (const name of reappeared) delete mergeBase[name];
      const headUnits = await fetchUnits(head.id);
      if (!headUnits) return result("unknown", "head-unreadable");
      const current = await view(localUnits, base);
      if (!current) return result("unknown", "base-incomplete");
      let merge;
      try { merge = await merge3(mergeBase, headUnits, current, root); }
      catch (e) {
        if (e instanceof Unresolvable) { paused = "unresolvable"; return result("paused", "unresolvable"); }
        throw e;
      }
      let newHead = head.id;
      if (!sameShas(await shasOf(merge.merged), headShas)) {
        if (!info.writable) return result("paused", info.reason || "read-only");
        const response = await put(head.id, merge.merged, "merge", headShas, extra);
        if (response.status !== 200 && response.status !== 201) return refusal(response);
        newHead = response.body.id;
      }
      if (!allowPull) {
        // The cloud holds everything now; this store catches up on the next
        // load, before the game starts.
        paused = "reload-to-sync";
        return result("merged", merge.kept.length ? "kept-twice" : null);
      }
      const localShas = await shasOf(localUnits);
      const mergedShas = await shasOf(merge.merged);
      const changed = {};
      for (const name of Object.keys(merge.merged)) {
        if (localShas[name] !== mergedShas[name]) changed[name] = merge.merged[name];
      }
      if (Object.keys(changed).length) {
        if (!options.pullEnabled || !info.pull) {
          paused = "load-offered";
          return result("offered", newHead);
        }
        if (beforePull && beforePull() === false) {
          paused = "reload-to-sync";
          return result("merged");
        }
        await options.local.pull(flatten(changed), localFiles);
      }
      for (const name of Object.keys(localUnits)) {
        if (!(name in merge.merged)) state.deletedElsewhere[name] = localShas[name];
      }
      for (const name of Object.keys(state.deletedElsewhere)) {
        if (name in merge.merged) delete state.deletedElsewhere[name];
      }
      await synced(newHead, merge.merged);
      return result(Object.keys(changed).length ? "pulled" : "merged", merge.kept.length ? "kept-twice" : null);
    }

    // Units the last sync had that this store no longer has, and the game did
    // not delete: remembered as missing (see `missing` at the top).
    function recordMissing(localUnits) {
      // A slot the game deleted and then wrote again is not deleted.
      const stillDeleted = state.pendingDeleted.filter((name) => !(name in localUnits));
      if (stillDeleted.length !== state.pendingDeleted.length) { state.pendingDeleted = stillDeleted; persist(); }
      if (!state.sync) return;
      for (const name of Object.keys(state.sync.units)) {
        if (name in localUnits || state.pendingDeleted.includes(name) || name in state.deletedElsewhere) continue;
        if (state.missing[name] === undefined) { state.missing[name] = state.sync.units[name]; persist(); }
      }
    }

    async function run(allowPull, beforePull, extra) {
      if (disabled) return result("disabled", disabled);
      if (paused && !allowPull && !(extra && extra.confirmShrink)) {
        // Paused, but still watching: a unit lost now must be recorded now.
        try { recordMissing(unitsOf(await options.local.read(), root)); } catch (e) { /* unreadable: nothing to record */ }
        return result("paused", paused);
      }
      if (allowPull) paused = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        let outcome;
        try { outcome = await syncOnce(allowPull, beforePull, extra); }
        catch (e) { return result("failed", String(e && e.message || e)); }
        if (outcome !== "retry") return outcome;
      }
      return result("failed", "busy");
    }

    return {
      load: (beforePull) => run(true, beforePull),
      afterSave: () => run(false),
      uploadAnyway: () => { paused = null; return run(false, null, { confirmShrink: true }); },
      disable: (reason) => { disabled = reason; },
      // Units the game deleted (paths named in a committed sync's `deleted`,
      // whose unit has no file left in the store).
      noteDeleted: (names) => {
        let changed = false;
        for (const name of names) if (!state.pendingDeleted.includes(name)) { state.pendingDeleted.push(name); changed = true; }
        if (changed) persist();
      },
      forget: () => { state = normalize(null); persist(); },
      get paused() { return paused; },
      get disabled() { return disabled; },
      get last() { return lastResult; },
      get state() { return state; },
    };
  }

  // -- The page -------------------------------------------------------------------

  function onArcade(where) {
    try {
      where = where || window.location;
      return where.protocol === "https:" && GAME_HOST.test(where.hostname) && where.hostname.indexOf("api.") !== 0;
    } catch (e) { return false; }
  }

  function withTimeout(promise, ms) {
    return Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve({ status: 0, body: null }), ms))]);
  }

  function request(method, path, body) {
    const init = { method: method, credentials: "include", headers: {} };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-Play-Client"] = "1";
      init.body = JSON.stringify(body);
    }
    return withTimeout(fetch(API + path, init).then(
      (response) => response.text().then((text) => {
        let parsed = null;
        try { parsed = text ? JSON.parse(text) : null; } catch (e) { parsed = null; }
        return { status: response.status, body: parsed };
      }),
      () => ({ status: 0, body: null })), REQUEST_TIMEOUT_MS);
  }

  function httpApi(store) {
    const base = "/v1/saves/" + encodeURIComponent(store);
    return {
      status: () => request("GET", base),
      upload: (body) => request("PUT", base, body),
      version: (id) => request("GET", base + "/versions/" + id),
      versions: () => request("GET", base + "/versions"),
      enroll: () => request("POST", base + "/enroll", {}),
      unenroll: () => request("DELETE", base + "/enroll", {}),
      session: () => request("GET", "/v1/session"),
    };
  }

  function storageState(key) {
    return {
      load: () => {
        try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
      },
      save: (value) => {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* the next load merges instead */ }
      },
    };
  }

  function randomId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return hex(crypto.getRandomValues(new Uint8Array(16)));
  }

  function deviceLabel() {
    const ua = navigator.userAgent || "";
    const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" :
      /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "A browser";
    const platform = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" :
      /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
    return platform ? browser + " on " + platform : browser;
  }

  // attach(): called by boot.js before the Worker starts.
  //   options: idbName, root, log, saves (the TakSaves.attach handle),
  //            savesApi (TakSaves internals), onStopOtherTabs()
  // Returns { preStart(beforePull) -> Promise, committed(deleted), restoreFailed(), stopped() }.
  function attach(options) {
    const idbName = options.idbName;
    const root = options.root;
    const log = options.log || "[tak]";
    const S = options.savesApi;
    const http = httpApi(idbName);
    const key = STATE_PREFIX + idbName;
    const meta = storageState(key + ":device");
    let device = meta.load();
    if (!device || typeof device.id !== "string") {
      device = { id: randomId(), label: deviceLabel() };
      meta.save(device);
    }
    let status = null;          // the last GET /v1/saves answer
    let uploading = null;
    let timer = null;
    let lastUpload = 0;
    let stopped = false;

    const local = {
      read: async () => {
        const entries = await S.readStore(idbName);
        return S.buildExport(idbName, entries).files;
      },
      pull: async (files, expected) => {
        // The import order of saves.js, without the dialog (RFC 0016 §4.3,
        // owner decision OQ6): validate, stop other tabs, back up and read
        // back, put-only, read back. Never a delete.
        const doc = { format: "tak-saves", version: 1, game: idbName, exported: new Date().toISOString(), files: files };
        const parsed = S.parseImport(JSON.stringify(doc), idbName, root);
        if (!parsed.ok) throw new Error("the cloud version is not a valid saves file: " + parsed.reason);
        if (options.onStopOtherTabs) await options.onStopOtherTabs();
        const current = await S.readStore(idbName);
        // The merge was computed from `expected`; if the store moved since (a
        // save in another tab), write nothing now and merge again next load.
        const now = S.buildExport(idbName, current).files;
        const a = Object.keys(now).sort(), b = Object.keys(expected).sort();
        if (a.join("\n") !== b.join("\n") || a.some((path) => canonical({ x: now[path] }) !== canonical({ x: expected[path] }))) {
          throw new Error("the saves changed while syncing; nothing was written");
        }
        if (current.size) await S.writeBackup(idbName, S.buildExport(idbName, current), "cloud");
        await S.mergeIntoStore(idbName, parsed.files);
        const after = await S.readStore(idbName);
        for (const [path, value] of parsed.files) {
          if (!after.has(path) || !S.sameValue(after.get(path), value)) throw new Error(path + " did not read back");
        }
        for (const path of current.keys()) if (!after.has(path)) throw new Error(path + " went missing");
      },
    };

    const engine = createEngine({
      store: idbName,
      root: root,
      device: device.id,
      label: device.label,
      pullEnabled: true,
      api: {
        status: () => http.status().then((response) => { if (response.status === 200) status = response.body; return response; }),
        upload: (body) => http.upload(body),
        version: (id) => http.version(id),
      },
      state: storageState(key),
      local: local,
    });

    function describe() {
      const last = engine.last;
      if (engine.disabled) return { ok: false, text: "Not backed up: this session could not read your saves." };
      if (engine.paused === "reload-to-sync") return { ok: true, text: "Backed up. Saves from another device will be added the next time the game loads." };
      if (engine.paused === "load-offered") return { ok: false, text: "Your account has newer saves from another device." };
      if (engine.paused === "shrink") return { ok: false, text: "Not backed up: this browser's saves are much smaller than your account's copy." };
      if (engine.paused === "quota") return { ok: false, text: "Cloud backup paused: your account's storage for this game is full." };
      if (engine.paused === "unresolvable") return { ok: false, text: "Not backed up: a save changed on two devices and could not be kept twice." };
      if (engine.paused) return { ok: false, text: "Not backed up: " + engine.paused + "." };
      if (!last) return { ok: true, text: "Cloud backup is on." };
      const at = last.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      if (["uploaded", "in-sync", "merged", "pulled", "empty"].includes(last.outcome)) {
        return { ok: true, text: "Backed up " + at + (last.detail === "kept-twice" ? ". A save changed on two devices was kept twice." : ".") };
      }
      if (last.outcome === "paused" && last.detail === "off") return { ok: false, text: "Cloud saves are off right now; your saves are kept in this browser." };
      if (last.outcome === "paused") return { ok: false, text: "Cloud backup is paused; your saves are kept in this browser." };
      return { ok: false, text: "Not backed up just now (" + (last.detail || last.outcome) + "); it will try again." };
    }

    function refreshBadge() {
      if (options.badge) {
        const d = describe();
        options.badge.textContent = enrolled() ? (d.ok ? "Backed up" : "Not backed up") : "";
        options.badge.title = enrolled() ? d.text : "";
      }
    }

    function enrolled() { return !!(status && status.enrolled === true); }

    function schedule() {
      if (stopped || !enrolled() || engine.disabled) return;
      if (timer) return;
      const wait = Math.max(2000, lastUpload + UPLOAD_SPACING_MS - Date.now());
      timer = setTimeout(flush, wait);
    }

    async function flush() {
      timer = null;
      if (stopped || uploading) { if (!stopped) schedule(); return; }
      lastUpload = Date.now();
      uploading = engine.afterSave();
      try { const outcome = await uploading; console.info(log, "cloud backup:", outcome, engine.last && engine.last.detail || ""); }
      finally { uploading = null; refreshBadge(); }
    }

    window.addEventListener("online", schedule);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden" && timer) { clearTimeout(timer); timer = null; flush(); }
    });

    // -- The Saves panel's cloud section --------------------------------------------

    const saves = options.saves;
    const hintKey = key + ":enrolled";
    function setHint(on) {
      try { if (on) localStorage.setItem(hintKey, "1"); else localStorage.removeItem(hintKey); } catch (e) {}
    }
    let badge = null;
    if (saves && saves.element) {
      badge = document.createElement("span");
      badge.className = "tak-saves-note tak-cloud-badge";
      badge.style.alignSelf = "center";
      badge.style.marginRight = ".75rem";
      saves.element.insertBefore(badge, saves.element.firstChild);
      options.badge = badge;
    }

    function unitLabel(name) {
      const match = SLOT.exec(name);
      return match ? "Slot " + match[1] : name;
    }

    async function section() {
      const ui = saves.ui;
      const nodes = [ui.element("h3", null, "Cloud backup")];
      const session = await http.session();
      if (session.status !== 200 || !session.body) {
        nodes.push(ui.element("p", "tak-saves-note", "Cloud backup can't be reached right now. Your saves in this browser are safe."));
        return nodes;
      }
      if (!session.body.signedIn) {
        nodes.push(ui.element("p", null, "Back up these saves to your account, so a cleared browser or a new device does not lose them."));
        nodes.push(ui.button("Sign in to back up", null, () => {
          location.href = API + "/signin?return=" + encodeURIComponent(location.href);
        }));
        return nodes;
      }
      const response = await http.status();
      if (response.status !== 200 || !response.body) {
        nodes.push(ui.element("p", "tak-saves-note", "Cloud backup can't be reached right now (" +
          (response.body && response.body.saves || "error " + response.status) + "). Your saves in this browser are safe."));
        return nodes;
      }
      status = response.body;
      if (!status.enrolled) {
        setHint(false);
        if (!status.allowed) {
          nodes.push(ui.element("p", "tak-saves-note", "Cloud backup isn't open to this account yet."));
        } else if (!status.writable) {
          nodes.push(ui.element("p", "tak-saves-note", "Cloud backup is paused right now. Your saves in this browser are safe."));
        } else {
          nodes.push(ui.element("p", null, "Back up this game's saves to " + (session.body.displayName || "your") +
            "'s account. They stay in this browser too."));
          nodes.push(ui.button("Turn on cloud backup", "tak-saves-primary", () => confirmEnroll(session.body.displayName)));
        }
        return nodes;
      }
      if (!engine.state.sync && !engine.last && !engine.disabled) {
        // The account backs this game up, but this browser has not synced
        // yet (cloud backup was turned on from another device): sync now
        // without touching this store; the next load brings the rest in.
        setHint(true);
        await cloudHandle.backUpNow();
      }
      setHint(true);
      const d = describe();
      nodes.push(ui.message(d.text, !d.ok));
      const row = ui.element("div", "tak-saves-actions");
      if (engine.paused === "reload-to-sync") {
        row.appendChild(ui.button("Reload now", "tak-saves-primary", () => location.reload()));
      }
      if (engine.paused === "load-offered" && status.head) {
        row.appendChild(ui.button("Load the newer saves", "tak-saves-primary", () => loadVersion(status.head.id)));
      }
      if (engine.paused === "shrink") {
        row.appendChild(ui.button("Back up this browser's saves anyway", "tak-saves-danger", async () => {
          await cloudHandle.uploadAnyway();
          saves.showMenu();
        }));
      }
      if (!engine.paused && !engine.disabled && status.writable) {
        row.appendChild(ui.button("Back up now", null, async () => {
          await cloudHandle.backUpNow();
          saves.showMenu();
        }));
      }
      row.appendChild(ui.button("Cloud versions", null, showVersions));
      row.appendChild(ui.button("Stop backing up", null, confirmStop));
      nodes.push(row);
      const manage = ui.element("p", "tak-saves-note");
      const link = document.createElement("a");
      link.href = API + "/account/saves";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Manage or delete your cloud saves";
      manage.appendChild(link);
      nodes.push(manage);
      return nodes;
    }

    async function confirmEnroll(displayName) {
      const ui = saves.ui;
      let units = [];
      try { units = Object.keys(unitsOf(await local.read(), root)).sort(); } catch (e) { /* listed as none */ }
      const nodes = [ui.element("h3", null, "Turn on cloud backup?")];
      nodes.push(ui.element("p", null, units.length
        ? "These saves will be copied to " + (displayName || "your") + "'s account:"
        : "There are no saves in this browser yet; new ones will be copied as you play."));
      if (units.length) {
        const list = ui.element("ul");
        for (const name of units) list.appendChild(ui.element("li", null, unitLabel(name)));
        nodes.push(list);
      }
      nodes.push(ui.element("p", "tak-saves-note",
        "Nothing in this browser is deleted or replaced. If your account already has saves from another device, " +
        "both are kept: a save that differs goes into a free slot. Saves from your other devices are added here " +
        "when the game next loads, after a backup of this browser's saves is kept."));
      ui.render(nodes, [
        ui.button("Turn on cloud backup", "tak-saves-primary", async () => {
          ui.render([ui.element("h3", null, "Turning on cloud backup…")], []);
          const response = await http.enroll();
          if (response.status !== 200) {
            saves.showMenu("Cloud backup could not be turned on (" + (response.body && response.body.error || "error " + response.status) +
              "). Nothing was changed.", true);
            return;
          }
          setHint(true);
          await cloudHandle.refresh();
          const outcome = await cloudHandle.backUpNow();
          if (outcome === "merged") {
            saves.showMenu("Cloud backup is on. Your account already had saves; reload to bring them into this browser.");
          } else {
            saves.showMenu("Cloud backup is on.");
          }
        }),
        ui.button("Cancel", null, () => saves.showMenu()),
      ]);
      ui.showDialog();
    }

    async function confirmStop() {
      const ui = saves.ui;
      ui.render([
        ui.element("h3", null, "Stop backing up?"),
        ui.element("p", null, "New saves will stay in this browser only. The copies already in your account are kept " +
          "until you delete them on your account page."),
      ], [
        ui.button("Stop backing up", "tak-saves-danger", async () => {
          const response = await http.unenroll();
          if (response.status === 200) { setHint(false); status = null; refreshBadge(); saves.showMenu("Cloud backup is off."); }
          else saves.showMenu("That did not work just now; cloud backup is still on.", true);
        }),
        ui.button("Cancel", null, () => saves.showMenu()),
      ]);
      ui.showDialog();
    }

    async function showVersions() {
      const ui = saves.ui;
      const response = await http.versions();
      if (response.status !== 200 || !response.body) {
        saves.showMenu("Your cloud versions can't be listed right now.", true);
        return;
      }
      const nodes = [ui.element("h3", null, "Cloud versions"),
        ui.element("p", "tak-saves-note", "Loading a version adds its saves to this browser (a save with the same slot is " +
          "replaced) after keeping a backup of what is here. Nothing is deleted.")];
      const versions = (response.body.versions || []).slice(0, 20);
      if (!versions.length) nodes.push(ui.element("p", null, "Nothing has been backed up yet."));
      for (const version of versions) {
        const when = String(version.createdAt || "").replace("T", " ").slice(0, 16);
        const saved = (version.units || []).map((u) => unitLabel(u.name)).join(", ") || "no saves";
        nodes.push(ui.element("p", null, when + " UTC \u00b7 " + (version.deviceLabel || "a browser") +
          (version.kind === "merge" ? " \u00b7 combined" : "") + " \u00b7 " + saved));
        const row = ui.element("div", "tak-saves-actions");
        row.appendChild(ui.button("Load this version", null, () => loadVersion(version.id)));
        row.appendChild(ui.button("Download", null, async () => {
          const file = await http.version(version.id);
          if (file.status === 200 && file.body) ui.download(file.body, idbName.replace(/[-_.](saves|files)$/, "") + "-cloud-" + when.slice(0, 10) + ".json");
        }));
        nodes.push(row);
      }
      ui.render(nodes, [ui.button("Back", null, () => saves.showMenu())]);
      ui.showDialog();
    }

    async function loadVersion(id) {
      const file = await http.version(id);
      if (file.status !== 200 || !file.body) {
        saves.showMenu("That version can't be fetched right now. Nothing was changed.", true);
        return;
      }
      // Exactly "Load saves from a file": validate, show what changes, back up, put-only, reload.
      saves.importText(JSON.stringify(file.body));
    }

    if (saves && saves.setExtraMenu) saves.setExtraMenu(section);

    const cloudHandle = {
      engine: engine,
      describe: describe,
      enrolled: enrolled,
      get status() { return status; },
      http: http,
      device: device,
      // Before the Worker starts. Resolves when the game may start; a pull
      // reloads the page instead and never resolves.
      preStart: async (beforePull) => {
        const outcome = await engine.load(beforePull);
        refreshBadge();
        console.info(log, "cloud sync:", outcome, engine.last && engine.last.detail || "");
        if (outcome === "pulled") {
          location.reload();
          return new Promise(() => {});
        }
        return outcome;
      },
      committed: (deletedPaths) => {
        if (stopped) return;
        if (Array.isArray(deletedPaths) && deletedPaths.length) {
          S.readStore(idbName).then((entries) => {
            const still = new Set();
            for (const path of entries.keys()) still.add(unitName(path, root));
            const names = [];
            for (const path of deletedPaths) {
              if (typeof path !== "string" || !path.startsWith(root + "/")) continue;
              const name = unitName(path, root);
              if (!still.has(name) && !names.includes(name)) names.push(name);
            }
            if (names.length) engine.noteDeleted(names);
            schedule();
          }, () => schedule());
        } else {
          schedule();
        }
      },
      restoreFailed: () => { engine.disable("restore-failed"); refreshBadge(); },
      stopped: () => { stopped = true; if (timer) clearTimeout(timer); },
      refresh: async () => { await http.status().then((r) => { if (r.status === 200) status = r.body; }); refreshBadge(); return status; },
      backUpNow: async () => { const outcome = await engine.afterSave(); refreshBadge(); return outcome; },
      uploadAnyway: async () => { const outcome = await engine.uploadAnyway(); refreshBadge(); return outcome; },
      refreshBadge: refreshBadge,
    };
    refreshBadge();
    return cloudHandle;
  }

  return {
    attach: attach,
    onArcade: onArcade,
    API: API,
    PRESTART_BUDGET_MS: PRESTART_BUDGET_MS,
    // Exposed for tests.
    _canonical: canonical,
    _sha256: sha256,
    _unitsOf: unitsOf,
    _merge3: merge3,
    _createEngine: createEngine,
    _Unresolvable: Unresolvable,
  };
})();
