'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { fn: decide } = require('../src/decide.js');

// Baseline healthy-iteration input; tests override what they exercise.
function input(over) {
  return Object.assign({
    passed: false, score: 0, recommend: '',
    act_ok: true, eval_ok: true,
    fail_streak: 0, infra_streak: 0, best_score: 0, replans_used: 0,
    spent_tokens: 0, act_tokens: 100, eval_tokens: 50, token_budget: 10000,
    max_retries: 3, max_replans: 1, enable_human: false, escalate_on_limit: false,
  }, over);
}

test('accepts when passed on a healthy iteration', () => {
  const r = decide(input({ passed: true, score: 1 }));
  assert.equal(r.decision, 'accept');
  assert.equal(r.status, 'succeeded');
});

test('accepts when passed arrives as string "true" (Conductor stringification)', () => {
  const r = decide(input({ passed: 'true', score: '0.9' }));
  assert.equal(r.decision, 'accept');
});

test('never accepts a passed verdict from an unhealthy iteration', () => {
  assert.notEqual(decide(input({ passed: true, act_ok: false })).decision, 'accept');
  assert.notEqual(decide(input({ passed: true, eval_ok: false })).decision, 'accept');
});

test('budget exhaustion stops with stopped_budget and accounts all tokens', () => {
  const r = decide(input({ spent_tokens: 9000, act_tokens: 800, eval_tokens: 300 }));
  assert.equal(r.decision, 'stop');
  assert.equal(r.status, 'stopped_budget');
  assert.equal(r.spent_tokens, 10100);
});

test('budget exhaustion escalates when human + escalate_on_limit enabled', () => {
  const r = decide(input({ spent_tokens: 99999, enable_human: true, escalate_on_limit: true }));
  assert.equal(r.decision, 'escalate');
  assert.equal(r.status, 'escalated');
});

test('token_budget 0 disables the budget guard', () => {
  const r = decide(input({ token_budget: 0, spent_tokens: 999999999 }));
  assert.equal(r.status, 'running');
});

test('budget check has priority over infra failure (bounds cost even when flaky)', () => {
  const r = decide(input({ act_ok: false, spent_tokens: 99999 }));
  assert.equal(r.status, 'stopped_budget');
});

test('actor infra failure retries and increments infra_streak only', () => {
  const r = decide(input({ act_ok: false, fail_streak: 1 }));
  assert.equal(r.decision, 'retry');
  assert.equal(r.infra_streak, 1);
  assert.equal(r.fail_streak, 1); // untouched: infra failures are not quality failures
  assert.match(r.reason, /Actor sub-workflow failed/);
});

test('evaluator infra failure is attributed to the evaluator', () => {
  const r = decide(input({ eval_ok: false }));
  assert.match(r.reason, /Evaluator sub-workflow failed/);
});

test('infra streak exhaustion stops with stopped_infra_failure (never replans)', () => {
  const r = decide(input({ act_ok: false, infra_streak: 2, max_retries: 3, replans_used: 0 }));
  assert.equal(r.decision, 'stop');
  assert.equal(r.status, 'stopped_infra_failure');
});

test('infra streak exhaustion escalates when human enabled', () => {
  const r = decide(input({ eval_ok: false, infra_streak: 2, enable_human: true }));
  assert.equal(r.decision, 'escalate');
  assert.equal(r.status, 'escalated');
});

test('a healthy iteration resets infra_streak', () => {
  const r = decide(input({ infra_streak: 2, score: 0.5 }));
  assert.equal(r.infra_streak, 0);
});

test('delegate recommendation switches actor and resets streaks', () => {
  const r = decide(input({ recommend: 'delegate', fail_streak: 2, infra_streak: 1 }));
  assert.equal(r.decision, 'delegate');
  assert.equal(r.fail_streak, 0);
  assert.equal(r.infra_streak, 0);
});

test('score improvement retries and resets fail_streak', () => {
  const r = decide(input({ score: 0.6, best_score: 0.4, fail_streak: 2 }));
  assert.equal(r.decision, 'retry');
  assert.equal(r.fail_streak, 0);
  assert.match(r.reason, /Score improved/);
});

test('no progress increments fail_streak and retries below the cap', () => {
  const r = decide(input({ score: 0.4, best_score: 0.4, fail_streak: 1 }));
  assert.equal(r.decision, 'retry');
  assert.equal(r.fail_streak, 2);
});

test('fail streak at cap replans when replans remain', () => {
  const r = decide(input({ score: 0.4, best_score: 0.4, fail_streak: 2, max_retries: 3 }));
  assert.equal(r.decision, 'replan');
  assert.equal(r.replans_used, 1);
  assert.equal(r.fail_streak, 0);
});

test('replans exhausted stops with stopped_no_progress', () => {
  const r = decide(input({ score: 0.4, best_score: 0.4, fail_streak: 2, replans_used: 1 }));
  assert.equal(r.decision, 'stop');
  assert.equal(r.status, 'stopped_no_progress');
});

test('replans exhausted escalates when human enabled', () => {
  const r = decide(input({ score: 0.4, best_score: 0.4, fail_streak: 2, replans_used: 1, enable_human: true }));
  assert.equal(r.decision, 'escalate');
});

test('survives all-null input with bounded defaults', () => {
  const r = decide({});
  assert.equal(r.decision, 'retry');
  assert.equal(r.status, 'running');
  assert.equal(r.fail_streak, 1);
});

test('coerces numeric strings (Conductor variables round-trip as strings)', () => {
  const r = decide(input({ score: '0.8', best_score: '0.5', fail_streak: '2' }));
  assert.equal(r.decision, 'retry');
  assert.equal(r.fail_streak, 0);
});
