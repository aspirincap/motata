"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const runtimePath = path.resolve(__dirname, "../lib/runtime.js");
const { ensureRuntime } = require(runtimePath);

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "motata-runtime-lock-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "package.json"), '{"version":"0.1.9"}');
  return root;
}
async function waitFor(check) {
  const deadline = Date.now() + 10000;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("Timed out waiting for test signal");
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

test("independent initializers wait for pip and reuse its completed runtime", { skip: process.platform === "win32", timeout: 20000 }, async t => {
  const root = fixture(t);
  const bin = path.join(root, "fake-bin");
  fs.mkdirSync(bin);
  // Every Python/pip operation is performed by Node, with no network or real Python.
  const fake = `#!${process.execPath}
const fs = require('node:fs'), path = require('node:path');
const args = process.argv.slice(2), root = process.env.TEST_ROOT;
if (args.includes('venv')) {
  const dir = args.at(-1);
  fs.mkdirSync(path.join(dir, 'bin'), {recursive:true});
  fs.copyFileSync(__filename, path.join(dir, 'bin/python'));
  fs.chmodSync(path.join(dir, 'bin/python'), 0o755);
} else if (args.includes('pip')) {
  fs.appendFileSync(path.join(root, 'installs'), 'install\\n');
  fs.writeFileSync(path.join(root, '.runtime/venv/in-progress'), 'untouched');
  fs.writeFileSync(path.join(root, 'pip-started'), '');
  const deadline = Date.now() + 10000;
  while (!fs.existsSync(path.join(root, 'release-pip'))) {
    if (Date.now() > deadline) process.exit(2);
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
  }
  fs.writeFileSync(path.join(root, '.runtime/venv/healthy'), '');
} else if (args.includes('-I')) {
  process.exit(fs.existsSync(path.join(root, '.runtime/venv/healthy')) ? 0 : 1);
}
`;
  fs.writeFileSync(path.join(bin, "python3"), fake, { mode: 0o755 });
  const children = [];
  t.after(() => { for (const child of children) if (child.exitCode === null) child.kill(); });
  function start(name) {
    const child = spawn(process.execPath, ["-e", `
      const fs = require('node:fs');
      fs.writeFileSync(${JSON.stringify(path.join(root, name + "-started"))}, '');
      console.log(JSON.stringify(require(${JSON.stringify(runtimePath)}).ensureRuntime(${JSON.stringify(root)})));
    `], { env: { PATH: bin, TEST_ROOT: root }, stdio: ["ignore", "pipe", "pipe"] });
    children.push(child);
    let stdout = "", stderr = "";
    child.stdout.on("data", data => { stdout += data; });
    child.stderr.on("data", data => { stderr += data; });
    return new Promise((resolve, reject) => {
      child.on("error", reject);
      child.on("close", code => {
        try { assert.equal(code, 0, stderr); resolve(JSON.parse(stdout)); } catch (error) { reject(error); }
      });
    });
  }
  const a = start("a");
  await waitFor(() => fs.existsSync(path.join(root, "pip-started")));
  const b = start("b");
  await waitFor(() => fs.existsSync(path.join(root, "b-started")));
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(children[1].exitCode, null);
  assert.equal(fs.readFileSync(path.join(root, ".runtime/venv/in-progress"), "utf8"), "untouched");
  assert.equal(fs.readFileSync(path.join(root, "installs"), "utf8"), "install\n");
  fs.writeFileSync(path.join(root, "release-pip"), "");
  const [first, second] = await Promise.all([a, b]);
  assert.equal(first.ok, true);
  assert.equal(first.installed, true);
  assert.equal(second.ok, true);
  assert.equal(second.installed, false);
  assert.equal(fs.readFileSync(path.join(root, "installs"), "utf8"), "install\n");
  assert.equal(fs.existsSync(path.join(root, ".runtime/init.lock")), false);
});

for (const owner of ["live", "empty", "malformed"]) {
  test(`${owner} lock times out without touching the venv`, t => {
    const root = fixture(t);
    const lock = path.join(root, ".runtime/init.lock");
    fs.mkdirSync(lock, { recursive: true });
    if (owner !== "empty") fs.writeFileSync(path.join(lock, owner === "live" ? `${process.pid}-abcdef` : "invalid"), "");
    const venv = path.join(root, ".runtime/venv");
    fs.mkdirSync(venv);
    fs.writeFileSync(path.join(venv, "sentinel"), "keep");
    const result = ensureRuntime(root, { lockTimeoutMs: 50 });
    assert.equal(result.ok, false);
    assert.match(result.message, /Timed out/);
    assert.equal(fs.readFileSync(path.join(venv, "sentinel"), "utf8"), "keep");
    assert.equal(fs.existsSync(lock), true);
  });
}

test("dead PID lock is reclaimed and released even when initialization throws", t => {
  const root = fixture(t);
  const pid = Number(spawnSync(process.execPath, ["-e", "console.log(process.pid)"], { encoding: "utf8" }).stdout.trim());
  assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
  const lock = path.join(root, ".runtime/init.lock");
  fs.mkdirSync(lock, { recursive: true });
  fs.writeFileSync(path.join(lock, `${pid}-abcdef`), "");
  // Fail before interpreter discovery: never access a real Python installation.
  fs.writeFileSync(path.join(root, "package.json"), "invalid json");
  assert.throws(() => ensureRuntime(root, { lockTimeoutMs: 50 }), SyntaxError);
  assert.equal(fs.existsSync(lock), false);
});
