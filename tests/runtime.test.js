"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../lib/runtime.js"), "utf8");

function fixture(t, { failPip = false, oldPython = false, failHealth = false } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "motata-runtime-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "package.json"), '{"version":"0.1.9"}');
  const calls = [];
  const fakeProcess = { pid: process.pid, kill: process.kill.bind(process), platform: "linux", env: {}, cwd: () => "/user/work", exit: code => { throw new Error(`exit:${code}`); } };
  const context = { require: name => name === "child_process" ? { spawnSync: (cmd, args, opts) => {
    calls.push({ cmd, args, opts });
    if (args.includes("venv")) fs.mkdirSync(path.join(root, ".runtime/venv"), { recursive: true });
    const bad = (failPip && args.includes("pip")) || (oldPython && args.includes("-c")) || (failHealth && args.includes("-I"));
    return { status: bad ? 1 : 0, stderr: bad ? "failed" : "" };
  } } : require(name), process: fakeProcess, console, __dirname, module: { exports: {} } };
  vm.runInNewContext(source, context);
  return { root, calls, runtime: context.module.exports };
}

test("failed install is retried; ready runtime is reused", t => {
  const f = fixture(t, { failPip: true });
  assert.equal(f.runtime.ensureRuntime(f.root).ok, false);
  assert.equal(f.runtime.ensureRuntime(f.root).ok, false);
  assert.equal(f.calls.filter(c => c.args.includes("pip")).length, 2);
  assert.equal(fs.existsSync(path.join(f.root, ".runtime/venv/.motata-ready")), false);
  const g = fixture(t);
  assert.equal(g.runtime.ensureRuntime(g.root).installed, true);
  assert.equal(g.runtime.ensureRuntime(g.root).installed, false);
  assert.equal(g.calls.filter(c => c.args.includes("pip")).length, 1);
});

test("an interrupted runtime without a marker is recreated", t => {
  const f = fixture(t);
  const dir = path.join(f.root, ".runtime/venv");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "partial-install"), "incomplete");
  assert.equal(f.runtime.ensureRuntime(f.root).installed, true);
  assert.equal(fs.existsSync(path.join(dir, "partial-install")), false);
});

test("a stale version marker triggers reinstall", t => {
  const f = fixture(t);
  assert.equal(f.runtime.ensureRuntime(f.root).ok, true);
  fs.writeFileSync(path.join(f.root, "package.json"), '{"version":"0.1.10"}');
  assert.equal(f.runtime.ensureRuntime(f.root).installed, true);
  assert.equal(f.calls.filter(c => c.args.includes("pip")).length, 2);
});

test("failed health check never leaves a ready marker", t => {
  const f = fixture(t, { failHealth: true });
  const dir = path.join(f.root, ".runtime/venv");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, ".motata-ready"), "0.1.9");
  assert.equal(f.runtime.ensureRuntime(f.root).ok, false);
  assert.equal(fs.existsSync(path.join(dir, ".motata-ready")), false);
  assert.equal(f.calls.filter(c => c.args.includes("pip")).length, 1);
  const probe = f.calls.find(c => c.args.includes("-I"));
  assert.match(probe.args.at(-1), /skills_compatibility\.json/);
  assert.match(probe.args.at(-1), /sys.version_info >= \(3, 11\)/);
});

test("rejects Python below 3.11 before creating venv", t => {
  const f = fixture(t, { oldPython: true });
  assert.match(f.runtime.ensureRuntime(f.root).message, />=3.11/);
  assert.equal(f.calls.some(c => c.args.includes("venv")), false);
});

test("launch preserves user cwd and explicitly supplies npm channel", t => {
  const f = fixture(t);
  assert.throws(() => f.runtime.runPythonModule("motata_cli", ["--help"], f.root), /exit:0/);
  const launch = f.calls.at(-1);
  assert.equal(launch.opts.cwd, "/user/work");
  assert.equal(launch.opts.env.MOTATA_INSTALL_METHOD, "npm");
});
