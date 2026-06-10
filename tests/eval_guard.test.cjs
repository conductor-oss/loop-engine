'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { fn: evalGuard } = require('../src/eval_guard.js');

test('passes a healthy verdict through, coercing types', () => {
  const v = evalGuard({ act_ok: true, passed: 'true', score: '0.85', feedback: 'good', recommend: '', tokens: '123' });
  assert.equal(v.act_ok, true);
  assert.equal(v.eval_ok, true);
  assert.equal(v.passed, true);
  assert.equal(v.score, 0.85);
  assert.equal(v.tokens, 123);
});

test('all-null evaluator output is an infra failure, not a meaningless verdict', () => {
  const v = evalGuard({ act_ok: true, passed: null, score: null, feedback: null, recommend: null, tokens: null });
  assert.equal(v.eval_ok, false);
  assert.equal(v.passed, false);
  assert.equal(v.score, 0);
  assert.match(v.feedback, /INFRA: the evaluator sub-workflow returned no verdict/);
});

test('a legitimate failing verdict (passed=false, score=0) is NOT an infra failure', () => {
  const v = evalGuard({ act_ok: true, passed: false, score: 0, feedback: '', tokens: 10 });
  assert.equal(v.eval_ok, true);
});

test('actor failure forces a non-accepting verdict with INFRA feedback', () => {
  const v = evalGuard({ act_ok: false, act_error: 'Actor sub-workflow failed or returned no result.',
                        passed: true, score: 1, feedback: 'looks great', tokens: 50 });
  assert.equal(v.act_ok, false);
  assert.equal(v.passed, false);
  assert.equal(v.score, 0);
  assert.match(v.feedback, /INFRA: the actor sub-workflow failed/);
  assert.match(v.feedback, /returned no result/);
});

test('feedback is truncated to a bounded length', () => {
  const v = evalGuard({ act_ok: true, passed: false, score: 0.2, feedback: 'x'.repeat(50000), tokens: 0 });
  assert.ok(v.feedback.length <= 2100);
  assert.match(v.feedback, /\[truncated\]$/);
});

test('negative scores clamp to 0; missing act_ok defaults to healthy', () => {
  const v = evalGuard({ passed: false, score: -3, feedback: 'f', tokens: 0 });
  assert.equal(v.score, 0);
  assert.equal(v.act_ok, true);
});
