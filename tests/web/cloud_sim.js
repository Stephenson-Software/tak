// The randomized three-device test of RFC 0016, for cloud.js's own engine.
//
//   node tests/web/cloud_sim.js <path to cloud.js> <seeds> <steps> [seed:steps,...]
//
// The optional list is extra seeds run first: each one found a real gap in an
// earlier cloud.js and stays here as a regression test.
//
// Three devices of one player run cloud.js's real engine (TakCloud._createEngine)
// against an in-memory server that applies arcade-social's upload rules (409 on
// a stale parent, 422 on a unit dropped without confirmRemoved, 200 with nothing
// stored for equal content, 503 for the kill switch, append-only versions).
// Steps: saves, deliberate deletions, units lost without a deletion, offline
// spells, failed restores, imports,
// kill-switch flips, cleared site data, and a Stage 1 device that never pulls.
//
// After every step it checks that every unit content any device ever committed
// is on some device, in a local backup, or in a server version (unless the
// player destroyed it before it reached anywhere else), that the versions form
// one parent chain, and that no load or import removed a local file.
//
// At the end, with every device back online and loaded twice, it checks the
// stronger property that catches merge bugs history alone would hide: every
// committed save the player did not move on from - by saving over it on a
// device that held it, deleting it, loading a file over it, or clearing the
// browser - is in the cloud's head. And the client never had an upload
// refused for leaving a save out (422 "removed").
//
// Prints "ok <runs>" or the failing seed and the last steps of its history.

"use strict";

globalThis.window = globalThis;
require(require("path").resolve(process.argv[2]));
const C = globalThis.TakCloud;
const SEEDS = parseInt(process.argv[3] || "100", 10);
const STEPS = parseInt(process.argv[4] || "120", 10);
const ROOT = "/saves";
const STORE = "night-ferry-saves";

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}

function unitName(path) { return path.slice(ROOT.length + 1).split("/", 1)[0]; }

// A unit's content without its name ("slot_3/save.json" -> "save.json").
function contentKey(unitFiles) {
  const parts = Object.keys(unitFiles).map((p) => p.slice(ROOT.length + 1).split("/").slice(1).join("/") + "=" + JSON.stringify(unitFiles[p]));
  return parts.sort().join("|");
}

class Server {
  constructor() { this.versions = []; this.headId = null; this.mode = "on"; this.refusedRemoved = 0; }
  async unitsOf(files) {
    const units = C._unitsOf(files, ROOT);
    const result = [];
    for (const name of Object.keys(units).sort()) result.push({ name: name, sha256: await C._sha256(C._canonical(units[name])) });
    return result;
  }
  head() { return this.versions.find((v) => v.id === this.headId) || null; }
  describe(v) { return { id: v.id, parent: v.parent, units: v.units, kind: v.kind }; }
  async status() {
    if (this.mode === "off") return { status: 503, body: { saves: "off" } };
    const head = this.head();
    return { status: 200, body: { enrolled: true, writable: this.mode === "on", reason: this.mode === "on" ? null : "paused", pull: true, head: head ? this.describe(head) : null } };
  }
  async upload(body) {
    if (this.mode !== "on") return { status: 503, body: { saves: this.mode === "off" ? "off" : "paused" } };
    if (body.parent !== this.headId) {
      const head = this.head();
      return { status: 409, body: { saves: "stale", head: head ? this.describe(head) : null } };
    }
    const units = await this.unitsOf(body.file.files);
    const head = this.head();
    if (head) {
      const names = new Set(units.map((u) => u.name));
      const dropped = head.units.map((u) => u.name).filter((n) => !names.has(n)).sort();
      const confirmed = (body.confirmRemoved || []).slice().sort();
      if (JSON.stringify(dropped) !== JSON.stringify(confirmed)) {
        this.refusedRemoved++;
        return { status: 422, body: { saves: "removed", removed: dropped } };
      }
      if (JSON.stringify(head.units) === JSON.stringify(units)) return { status: 200, body: { id: head.id, created: false } };
    } else if (!units.length) {
      return { status: 200, body: { id: null, created: false } };
    }
    const version = { id: this.versions.length + 1, parent: this.headId, units: units, files: JSON.parse(JSON.stringify(body.file.files)), kind: body.kind };
    this.versions.push(version);
    this.headId = version.id;
    return { status: 201, body: { id: version.id, created: true } };
  }
  async version(id) {
    if (this.mode === "off") return { status: 503, body: null };
    const v = this.versions.find((x) => x.id === id);
    if (!v) return { status: 404, body: null };
    return { status: 200, body: { format: "tak-saves", version: 1, game: STORE, files: JSON.parse(JSON.stringify(v.files)) } };
  }
}

class Device {
  constructor(name, server, pull) {
    this.name = name;
    this.server = server;
    this.online = true;
    this.store = {};       // IndexedDB
    this.backups = [];     // <idb>.tak-backups
    this.saved = null;     // localStorage
    this.sessionFailed = false;
    this.pullEnabled = pull;
    this.newEngine();
  }
  newEngine() {
    const device = this;
    const api = {
      status: () => device.online ? device.server.status() : Promise.resolve({ status: 0, body: null }),
      upload: (b) => device.online ? device.server.upload(b) : Promise.resolve({ status: 0, body: null }),
      version: (id) => device.online ? device.server.version(id) : Promise.resolve({ status: 0, body: null }),
    };
    this.engine = C._createEngine({
      store: STORE, root: ROOT, device: "device-" + this.name, label: this.name, pullEnabled: this.pullEnabled, api: api,
      state: { load: () => (device.saved ? JSON.parse(device.saved) : null), save: (v) => { device.saved = JSON.stringify(v); } },
      local: {
        read: async () => {
          if (device.sessionFailed) throw new Error("restore failed");
          return Object.assign({}, device.store);
        },
        pull: async (files, expected) => {
          if (JSON.stringify(sorted(device.store)) !== JSON.stringify(sorted(expected))) throw new Error("moved");
          if (Object.keys(device.store).length) device.backups.push(Object.assign({}, device.store));
          for (const path of Object.keys(files)) device.store[path] = files[path];   // put-only
        },
      },
    });
    if (this.sessionFailed) this.engine.disable("restore-failed");
  }
  save(unit, content) {
    if (this.sessionFailed) return false;
    const old = C._unitsOf(this.store, ROOT)[unit];
    if (old) this.world.superseded.add(contentKey(old));
    const path = ROOT + "/" + unit + "/save.json";
    const deleted = Object.keys(this.store).filter((p) => unitName(p) === unit && p !== path);
    for (const p of deleted) delete this.store[p];
    this.store[path] = content;
    return true;
  }
  remove(unit) {
    if (this.sessionFailed) return false;
    const paths = Object.keys(this.store).filter((p) => unitName(p) === unit);
    if (!paths.length) return false;
    this.world.deleted.add(contentKey(C._unitsOf(this.store, ROOT)[unit]));
    for (const p of paths) delete this.store[p];
    this.engine.noteDeleted([unit]);
    return true;
  }
  async load(failed) {
    this.sessionFailed = !!failed;
    this.newEngine();   // a reload: a new page, the same localStorage
    if (failed) return "restore-failed";
    return this.engine.load(() => true);
  }
  importFile(files) {
    const before = C._unitsOf(this.store, ROOT);
    const incoming = C._unitsOf(files, ROOT);
    for (const name of Object.keys(incoming)) {
      if (before[name]) this.world.superseded.add(contentKey(before[name]));   // the player loaded over it
    }
    if (Object.keys(this.store).length) this.backups.push(Object.assign({}, this.store));
    Object.assign(this.store, files);
  }
  clear() { this.store = {}; this.backups = []; this.saved = null; this.newEngine(); }
}

function sorted(o) { const r = {}; for (const k of Object.keys(o).sort()) r[k] = o[k]; return r; }

async function run(seed, steps) {
  const random = rng(seed * 2654435761);
  const server = new Server();
  const world = { superseded: new Set(), deleted: new Set(), neverUploaded: new Set() };
  Device.prototype.world = world;
  const devices = [new Device("a", server, true), new Device("b", server, true), new Device("c", server, false)];
  const committed = new Set();
  const released = new Set();
  const history = [];
  let counter = 0;

  function everywhere(exclude) {
    const found = new Set();
    for (const v of server.versions) for (const files of Object.values(C._unitsOf(v.files, ROOT))) found.add(contentKey(files));
    for (const d of devices) {
      if (d === exclude) continue;
      for (const files of Object.values(C._unitsOf(d.store, ROOT))) found.add(contentKey(files));
      for (const b of d.backups) for (const files of Object.values(C._unitsOf(b, ROOT))) found.add(contentKey(files));
    }
    return found;
  }

  function releaseOverwritten(device, before) {
    const now = C._unitsOf(device.store, ROOT);
    const elsewhere = everywhere();
    for (const [name, files] of Object.entries(C._unitsOf(before, ROOT))) {
      if (!(name in now) || contentKey(now[name]) !== contentKey(files)) {
        const key = contentKey(files);
        if (!elsewhere.has(key)) released.add(key);
      }
    }
  }

  function check() {
    const present = everywhere();
    for (const key of committed) {
      if (!present.has(key) && !released.has(key)) {
        throw new Error("seed " + seed + ": a committed save was lost: " + key + "\n" + history.slice(-25).join("\n"));
      }
    }
    let previous = null;
    for (const v of server.versions) {
      if (v.parent !== previous) throw new Error("seed " + seed + ": version " + v.id + " not based on head " + previous);
      previous = v.id;
    }
  }

  function noFileRemoved(device, before, what) {
    for (const path of Object.keys(before)) {
      if (!(path in device.store)) throw new Error("seed " + seed + ": " + what + " removed " + path + "\n" + history.slice(-25).join("\n"));
    }
  }

  for (let step = 0; step < steps; step++) {
    const device = devices[Math.floor(random() * devices.length)];
    const roll = random();
    const before = Object.assign({}, device.store);
    if (roll < 0.40) {
      counter++;
      const names = Array.from(new Set(Object.keys(device.store).map(unitName))).sort();
      const unit = names.length && random() < 0.6 ? names[Math.floor(random() * names.length)] : "slot_" + (1 + Math.floor(random() * 6));
      const content = device.name + "-" + counter + "-" + "x".repeat(20 + Math.floor(random() * 10));
      if (device.save(unit, content)) {
        committed.add(contentKey({ [ROOT + "/" + unit + "/save.json"]: content }));
        releaseOverwritten(device, before);
        if (random() < 0.7) history.push(device.name + ": save " + unit + " -> " + await device.engine.afterSave());
        else history.push(device.name + ": save " + unit + " (deferred)");
      }
    } else if (roll < 0.48) {
      const names = Array.from(new Set(Object.keys(device.store).map(unitName))).sort();
      if (names.length) {
        const unit = names[Math.floor(random() * names.length)];
        if (device.remove(unit)) {
          releaseOverwritten(device, before);
          history.push(device.name + ": delete " + unit + " -> " + await device.engine.afterSave());
        }
      }
    } else if (roll < 0.68) {
      const failed = random() < 0.1;
      history.push(device.name + ": load" + (failed ? " (restore failed)" : "") + " -> " + await device.load(failed));
      noFileRemoved(device, before, "a load");
    } else if (roll < 0.76) {
      device.online = !device.online;
      history.push(device.name + ": online=" + device.online);
    } else if (roll < 0.82) {
      server.mode = ["on", "on", "readonly", "off"][Math.floor(random() * 4)];
      history.push("-: kill switch " + server.mode);
    } else if (roll < 0.88) {
      if (server.versions.length) {
        const v = server.versions[Math.floor(random() * server.versions.length)];
        device.importFile(JSON.parse(JSON.stringify(v.files)));
        history.push(device.name + ": import version " + v.id);
        noFileRemoved(device, before, "an import");
      }
    } else if (roll < 0.885) {
      // A unit vanishes from the store without the game deleting it (the
      // Night Ferry empty-slot report; storage trouble). It must never be
      // dropped from the cloud because of that.
      const names = Array.from(new Set(Object.keys(device.store).map(unitName))).sort();
      if (names.length) {
        const unit = names[Math.floor(random() * names.length)];
        for (const p of Object.keys(device.store).filter((q) => unitName(q) === unit)) delete device.store[p];
        const elsewhere = everywhere();
        const key = contentKey(C._unitsOf(before, ROOT)[unit]);
        if (!elsewhere.has(key)) released.add(key);
        // Lost before it ever reached the cloud: it can survive only in a local
        // backup, so the cloud's head is not expected to have it.
        if (!server.versions.some((v) => Object.values(C._unitsOf(v.files, ROOT)).some((f) => contentKey(f) === key))) {
          world.neverUploaded.add(key);
        }
        history.push(device.name + ": lost track of " + unit + " -> " + await device.engine.afterSave());
      }
    } else if (roll < 0.90) {
      const elsewhere = everywhere(device);
      for (const files of Object.values(C._unitsOf(device.store, ROOT))) if (!elsewhere.has(contentKey(files))) released.add(contentKey(files));
      for (const b of device.backups) for (const files of Object.values(C._unitsOf(b, ROOT))) if (!elsewhere.has(contentKey(files))) released.add(contentKey(files));
      device.clear();
      history.push(device.name + ": cleared site data");
    } else if (roll < 0.93 && device.engine.paused === "load-offered") {
      const head = server.head();
      if (head) {
        device.importFile(JSON.parse(JSON.stringify(head.files)));
        history.push(device.name + ": load this version " + head.id);
      }
    } else {
      history.push(device.name + ": flush -> " + await device.engine.afterSave());
    }
    check();
  }
  server.mode = "on";
  for (const d of devices) d.online = true;
  // Load every device until a whole round stores no new version (a fixed
  // point), at most six rounds: a Stage 1 device's merge can need one more
  // round to reach the others.
  for (let round = 0; round < 6; round++) {
    const before = server.versions.length;
    for (const d of devices) { history.push(d.name + ": final load -> " + await d.load(false)); check(); }
    if (round > 0 && server.versions.length === before) break;
  }
  // Nothing the player did not move on from is missing from the cloud's head.
  const head = server.head();
  const inHead = new Set(head ? Object.values(C._unitsOf(head.files, ROOT)).map(contentKey) : []);
  for (const key of committed) {
    if (released.has(key) || world.superseded.has(key) || world.deleted.has(key) || world.neverUploaded.has(key)) continue;
    if (!inHead.has(key)) throw new Error("seed " + seed + ": a save the player never moved on from is not in the cloud's head: " + key + "\n" + history.slice(-30).join("\n"));
  }
  if (server.refusedRemoved) throw new Error("seed " + seed + ": the client had " + server.refusedRemoved + " upload(s) refused for leaving a save out");
  // Stage 2 devices end holding every unit the cloud's head has.
  if (head) {
    for (const d of devices.filter((x) => x.pullEnabled)) {
      for (const [path, content] of Object.entries(head.files)) {
        if (d.store[path] !== content) throw new Error("seed " + seed + ": device " + d.name + " did not converge on " + path + "\n" + history.slice(-25).join("\n"));
      }
    }
  }
}

const FIXED = (process.argv[5] || "").split(",").filter(Boolean).map((item) => item.split(":").map(Number));

(async () => {
  for (const [seed, steps] of FIXED) {
    try { await run(seed, steps); }
    catch (e) { console.log("FAIL (fixed) " + e.message); process.exit(1); }
  }
  for (let seed = 1; seed <= SEEDS; seed++) {
    try { await run(seed, STEPS); }
    catch (e) { console.log("FAIL " + e.message); process.exit(1); }
  }
  console.log("ok " + SEEDS + " + " + FIXED.length + " fixed");
})();
