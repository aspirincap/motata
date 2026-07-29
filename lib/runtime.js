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
    const probe = runChecked(candidate.cmd, [...candidate.args, "--version"]);
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

function ensureRuntime(root = packageRoot()) {
  const pythonInVenv = venvPython(root);
  if (fs.existsSync(pythonInVenv)) {
    return { ok: true, python: pythonInVenv, installed: false };
  }

  const candidate = findPython();
  if (!candidate) {
    return {
      ok: false,
      message: "Python 3 is required to bootstrap motata from npm, but no python3/python interpreter was found."
    };
  }

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
    cwd: root,
    env: process.env
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
