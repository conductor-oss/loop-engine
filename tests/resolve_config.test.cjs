'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { fn: resolveConfig } = require('../src/resolve_config.js');

const REQUIRED = {
  objective: 'do a thing', acceptance_criteria: 'thing is done',
  llm_provider: 'anthropic', llm_model: 'claude-opus-4-7',
};

test('minimal valid input resolves with default preset and default workflows', () => {
  const c = resolveConfig({ ...REQUIRED });
  assert.equal(c.valid, 'true');
  assert.equal(c.effort, 'default');
  assert.equal(c.max_iterations, 6);
  assert.equal(c.max_retries, 3);
  assert.equal(c.max_replans, 1);
  assert.equal(c.token_budget, 200000);
  assert.equal(c.planner_workflow, 'loop_planner');
  assert.equal(c.actor_workflow, 'loop_actor');
  assert.equal(c.delegate_actor_workflow, 'loop_actor');
  assert.equal(c.evaluator_workflow, 'loop_evaluator');
  assert.equal(c.evaluator_llm_model, 'claude-opus-4-7');
  assert.equal(c.enable_human, false);
});

test('medium and high efforts scale the work the loop may do', () => {
  const m = resolveConfig({ ...REQUIRED, effort: 'medium' });
  assert.deepEqual(
    [m.max_iterations, m.max_retries, m.max_replans, m.token_budget],
    [12, 4, 2, 500000]);
  const h = resolveConfig({ ...REQUIRED, effort: 'high' });
  assert.deepEqual(
    [h.max_iterations, h.max_retries, h.max_replans, h.token_budget],
    [24, 5, 3, 2000000]);
});

test('effort is case/alias tolerant; unknown falls back to default', () => {
  assert.equal(resolveConfig({ ...REQUIRED, effort: ' MED ' }).effort, 'medium');
  assert.equal(resolveConfig({ ...REQUIRED, effort: 'High' }).effort, 'high');
  assert.equal(resolveConfig({ ...REQUIRED, effort: 'turbo' }).effort, 'default');
});

test('explicit inputs always override the effort preset', () => {
  const c = resolveConfig({ ...REQUIRED, effort: 'high', max_iterations: 3, token_budget: 1000 });
  assert.equal(c.max_iterations, 3);
  assert.equal(c.token_budget, 1000);
  assert.equal(c.max_retries, 5); // unset knob still comes from the preset
});

test('explicit token_budget 0 disables the budget (and survives clamping)', () => {
  assert.equal(resolveConfig({ ...REQUIRED, token_budget: 0 }).token_budget, 0);
});

test('knobs are clamped so bad input can never produce an unbounded loop', () => {
  const c = resolveConfig({ ...REQUIRED, max_iterations: 100000, max_retries: -5, max_replans: 'garbage' });
  assert.equal(c.max_iterations, 200);
  assert.equal(c.max_retries, 1);
  assert.equal(c.max_replans, 1); // garbage -> preset default
});

test('max_iterations 0 is clamped to 1 (the silent zero-iteration loop bug)', () => {
  assert.equal(resolveConfig({ ...REQUIRED, max_iterations: 0 }).max_iterations, 1);
});

test('missing required inputs are reported precisely and flagged invalid', () => {
  const c = resolveConfig({ llm_provider: 'anthropic' });
  assert.equal(c.valid, 'false');
  assert.match(c.error, /objective/);
  assert.match(c.error, /acceptance_criteria/);
  assert.match(c.error, /llm_model/);
  assert.doesNotMatch(c.error, /llm_provider/);
});

test('evaluator_llm_model decorrelates the judge when provided', () => {
  const c = resolveConfig({ ...REQUIRED, evaluator_llm_model: 'claude-sonnet-4-6' });
  assert.equal(c.evaluator_llm_model, 'claude-sonnet-4-6');
});

test('delegate defaults to the resolved actor, not the built-in', () => {
  const c = resolveConfig({ ...REQUIRED, actor_workflow: 'my_actor' });
  assert.equal(c.delegate_actor_workflow, 'my_actor');
});

test('pre-planner is off by default and on only when a workflow name is given', () => {
  const off = resolveConfig({ ...REQUIRED });
  assert.equal(off.pre_planner_workflow, '');
  assert.equal(off.has_pre_planner, 'false');
  const on = resolveConfig({ ...REQUIRED, pre_planner_workflow: 'my_pre_planner' });
  assert.equal(on.pre_planner_workflow, 'my_pre_planner');
  assert.equal(on.has_pre_planner, 'true');
});

test('boolean flags accept string forms', () => {
  const c = resolveConfig({ ...REQUIRED, enable_human: 'true', escalate_on_limit: 'false' });
  assert.equal(c.enable_human, true);
  assert.equal(c.escalate_on_limit, false);
});
