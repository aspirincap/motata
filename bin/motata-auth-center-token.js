#!/usr/bin/env node
"use strict";

const { runPythonModule } = require("../lib/runtime");

runPythonModule("motata_cli.auth_center.fetch_token", process.argv.slice(2));
