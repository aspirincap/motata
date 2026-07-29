#!/usr/bin/env node
"use strict";

const { ensureRuntime } = require("../lib/runtime");

if (process.env.MOTATA_SKIP_PYTHON_INSTALL === "1") {
  process.exit(0);
}

const result = ensureRuntime();
if (!result.ok) {
  console.error(result.message);
  process.exit(1);
}

const source = process.env.MOTATA_SKILLS_SOURCE || "https://skill.motata.one";
const lines = [
  "[motata] Runtime bootstrap complete.",
  `[motata] Skills source: ${source}`,
  "[motata] Next steps:",
  "  - Check sync status: motata update --check",
  "  - Sync Motata skills: motata update --skills-only",
  "  - Update CLI + skills: motata update",
];
console.error(lines.join("\n"));
