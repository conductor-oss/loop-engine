'use strict';
// Guards against drift between src/*.js (canonical, tested) and the inlined
// expressions inside workflows/loop_engine.json. If this fails, run:
//   node scripts/build.mjs
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { join } = require('node:path');

test('workflows/loop_engine.json is in sync with src/', () => {
  execFileSync(process.execPath, [join(__dirname, '..', 'scripts', 'build.mjs'), '--check'], {
    stdio: 'pipe',
  });
  assert.ok(true);
});
