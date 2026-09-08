"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function packageRoot() {
  return path.resolve(__dirname, "..");
}

function isWindows() {
  return process.platform === "win32";
}

function venvDir(root = packageRoot()) {
  return path.join(root, ".runtime", "venv");
}

function pythonCandidates() {
  if (isWindows()) {
    return [
      { cmd: "py", args: ["-3"] },
      { cmd: "python", args: [] },
      { cmd: "python3", args: [] }
    ];
  }
  return [
    { cmd: "python3", args: [] },
    { cmd: "python", args: [] }
  ];
}

function runChecked(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, {
    stdio: "pipe",
    encoding: "utf8",
    ...options
  });
  if (result.error) {
    return { ok: false, error: result.error };
  }
  return {
    ok: result.status === 0,
    status: result.status,
    stdout: result.stdout || "",
    stderr: result.stderr || ""
  };
}

function findPython() {
  for (const candidate of pythonCandidates()) {
    const probe = runChecked(candidate.cmd, [...candidate.args, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"]);
    if (probe.ok) {
      return candidate;
    }
  }
  return null;
}

function venvPython(root = packageRoot()) {
  if (isWindows()) {
    return path.join(venvDir(root), "Scripts", "python.exe");
  }
  return path.join(venvDir(root), "bin", "python");
}

function venvBin(root = packageRoot(), command) {
  if (isWindows()) {
    return path.join(venvDir(root), "Scripts", `${command}.exe`);
  }
  return path.join(venvDir(root), "bin", command);
}

// Never expire a live (or unverifiable) owner based on lock age. An empty or
// malformed lock is also left alone: its creator may still be publishing ownership.
function acquireRuntimeLock(root, timeoutMs) {
  const lock = path.join(root, ".runtime", "init.lock");
  const owner = `${process.pid}-${require("crypto").randomUUID()}`;
  const deadline = Date.now() + timeoutMs;
  fs.mkdirSync(path.dirname(lock), { recursive: true });
  for (;;) {
    try {
      fs.mkdirSync(lock);
      fs.writeFileSync(path.join(lock, owner), "", { flag: "wx" });
      return () => {
        fs.unlinkSync(path.join(lock, owner));
        fs.rmdirSync(lock);
      };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }

    try {
      const entries = fs.readdirSync(lock);
      if (entries.length === 1 && /^[1-9]\d*-[0-9a-f-]+$/.test(entries[0])) {
        const pid = Number(entries[0].split("-")[0]);
        let dead = false;
        try { process.kill(pid, 0); } catch (error) { dead = error.code === "ESRCH"; }
        if (dead) {
          // Unlink the unique ownership entry first. Only its successful remover
          // may rmdir; competing stale-lock readers cannot remove a new owner's lock.
          fs.unlinkSync(path.join(lock, entries[0]));
          fs.rmdirSync(lock);
          continue;
        }
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (Date.now() >= deadline) return null;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50);
  }
}

function ensureRuntime(root = packageRoot(), { lockTimeoutMs = 120000 } = {}) {
  if (!Number.isFinite(lockTimeoutMs) || lockTimeoutMs < 0) {
    throw new TypeError("lockTimeoutMs must be a finite non-negative number");
  }
  const release = acquireRuntimeLock(root, lockTimeoutMs);
  if (!release) return { ok: false, message: "Timed out waiting for the npm Python runtime initialization lock." };
  try {
    // Health and marker checks must happen after acquiring the lock, including
    // for waiters whose predecessor has just completed installation.
    return ensureRuntimeLocked(root);
  } finally {
    release();
  }
}

function ensureRuntimeLocked(root) {
  const pythonInVenv = venvPython(root);
  const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
  const marker = path.join(venvDir(root), ".motata-ready");
  const healthCheck = () => runChecked(pythonInVenv, ["-I", "-c",
    "import sys, json, motata_cli, requests, bs4, six; from importlib.resources import files; " +
    `assert sys.version_info >= (3, 11); assert motata_cli.__version__ == ${JSON.stringify(version)}; ` +
    "assert json.loads(files('motata_cli').joinpath('skills_compatibility.json').read_text())['releases']"
  ], { cwd: path.dirname(root) }).ok;
  if (fs.existsSync(marker) && fs.readFileSync(marker, "utf8") === version && healthCheck()) {
    return { ok: true, python: pythonInVenv, installed: false };
  }
  fs.rmSync(marker, { force: true });

  const candidate = findPython();
  if (!candidate) {
    return {
      ok: false,
      message: "Python >=3.11 is required to bootstrap motata from npm; no compatible interpreter was found."
    };
  }

  // Recreate incomplete or incompatible environments, including interrupted pip installs.
  fs.rmSync(venvDir(root), { recursive: true, force: true });
  fs.mkdirSync(path.dirname(venvDir(root)), { recursive: true });

  const venvResult = runChecked(candidate.cmd, [...candidate.args, "-m", "venv", venvDir(root)], {
    cwd: root,
    env: process.env
  });
  if (!venvResult.ok) {
    return {
      ok: false,
      message: `Failed to create Python virtualenv.\n${venvResult.stderr || venvResult.stdout}`.trim()
    };
  }

  const env = {
    ...process.env,
    PIP_DISABLE_PIP_VERSION_CHECK: "1"
  };
  const installResult = runChecked(
    pythonInVenv,
    ["-m", "pip", "install", "."],
    { cwd: root, env }
  );
  if (!installResult.ok) {
    return {
      ok: false,
      message: `Failed to install motata Python package into the npm runtime.\n${installResult.stderr || installResult.stdout}`.trim()
    };
  }

  if (!healthCheck()) {
    return { ok: false, message: "The npm Python runtime failed its post-install health check." };
  }
  fs.writeFileSync(marker, version);
  return { ok: true, python: pythonInVenv, installed: true };
}

function runPythonModule(moduleName, argv, root = packageRoot()) {
  const runtime = ensureRuntime(root);
  if (!runtime.ok) {
    console.error(runtime.message);
    process.exit(1);
  }

  const result = spawnSync(runtime.python, ["-m", moduleName, ...argv], {
    stdio: "inherit",
    cwd: process.cwd(),
    env: { ...process.env, MOTATA_INSTALL_METHOD: "npm" }
  });

  if (result.error) {
    console.error(String(result.error));
    process.exit(1);
  }
  process.exit(result.status == null ? 1 : result.status);
}

module.exports = {
  ensureRuntime,
  packageRoot,
  runPythonModule,
  venvBin
};
