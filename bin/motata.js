#!/usr/bin/env node
"use strict";

const { runPythonModule } = require("../lib/runtime");

runPythonModule("motata_cli", process.argv.slice(2));
