#!/usr/bin/env node
// Inlines the canonical INLINE-task sources (src/*.js) into workflows/loop_engine.json.
//
//   node scripts/build.mjs          rewrite the workflow JSON from src/
//   node scripts/build.mjs --check  exit 1 if the JSON has drifted from src/
//
// Each src file defines one ES5 function plus a module.exports block below the
// `// --- exports` marker; the marker and everything after it are stripped, the
// remaining code is comment-stripped and collapsed to one line, and the result
// is wrapped as `(function(){ <body> return <fn>($); })();` — the exact form
// Conductor's graaljs INLINE evaluator executes.

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const TARGETS = [
  { taskRef: 'resolve_config', src: 'src/resolve_config.js', fn: 'resolveConfig' },
  { taskRef: 'decide',         src: 'src/decide.js',         fn: 'decide' },
  { taskRef: 'eval_guard',     src: 'src/eval_guard.js',     fn: 'evalGuard' },
];
const WORKFLOW = 'workflows/loop_engine.json';
const EXPORT_MARKER = '// --- exports';

export function inlineExpression(source, fnName) {
  const markerAt = source.indexOf(EXPORT_MARKER);
  if (markerAt === -1) throw new Error(`export marker not found (expected "${EXPORT_MARKER}")`);
  const body = source
    .slice(0, markerAt)
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !l.startsWith('//'))
    .join(' ');
  return `(function(){ ${body} return ${fnName}($); })();`;
}

function* walkTasks(tasks) {
  for (const t of tasks ?? []) {
    yield t;
    if (t.loopOver) yield* walkTasks(t.loopOver);
    for (const branch of Object.values(t.decisionCases ?? {})) yield* walkTasks(branch);
    if (t.defaultCase) yield* walkTasks(t.defaultCase);
  }
}

function main(check) {
  const wfPath = join(root, WORKFLOW);
  const wf = JSON.parse(readFileSync(wfPath, 'utf8'));
  const drift = [];

  for (const { taskRef, src, fn } of TARGETS) {
    const expr = inlineExpression(readFileSync(join(root, src), 'utf8'), fn);
    const task = [...walkTasks(wf.tasks)].find((t) => t.taskReferenceName === taskRef);
    if (!task) throw new Error(`task "${taskRef}" not found in ${WORKFLOW}`);
    if (task.inputParameters.expression !== expr) {
      drift.push(taskRef);
      task.inputParameters.expression = expr;
    }
  }

  if (check) {
    if (drift.length) {
      console.error(`DRIFT: ${WORKFLOW} task(s) [${drift.join(', ')}] do not match src/. Run: node scripts/build.mjs`);
      process.exit(1);
    }
    console.log(`${WORKFLOW} is in sync with src/.`);
    return;
  }

  writeFileSync(wfPath, JSON.stringify(wf, null, 2) + '\n');
  console.log(drift.length ? `updated ${WORKFLOW}: [${drift.join(', ')}]` : `${WORKFLOW} already in sync.`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.includes('--check'));
}
