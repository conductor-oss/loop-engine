// Canonical config resolution for the loop_engine `resolve_config` task.
//
// THIS FILE IS THE SOURCE OF TRUTH — see scripts/build.mjs. ES5 only (graaljs).
//
// Responsibilities:
//   * Effort presets: `effort` = default | medium | high scales how much work
//     the loop may do on an open-ended problem. Explicit inputs ALWAYS override
//     the preset; the preset only fills what the caller left unset.
//   * Validation: required inputs missing -> { valid: 'false', error } and the
//     engine TERMINATEs with a precise message instead of silently looping zero
//     times.
//   * Defaults + clamps: every knob is coerced to a number and clamped to a
//     sane range, so a typo'd input can never produce an unbounded loop.
//
// Note: token_budget of 0 explicitly disables the budget guard (documented).

function resolveConfig($) {
  function num(v, d) {
    if (v === null || v === undefined || v === '') { return d; }
    var n = Number(v);
    return isNaN(n) ? d : n;
  }
  function clamp(n, lo, hi) { if (n < lo) { return lo; } if (n > hi) { return hi; } return n; }
  function flag(v, d) {
    if (v === true || v === 'true') { return true; }
    if (v === false || v === 'false') { return false; }
    return d;
  }
  function str(v, d) { return (typeof v === 'string' && v.length > 0) ? v : d; }

  var PRESETS = {
    'default': { max_iterations: 6,  max_retries: 3, max_replans: 1, token_budget: 200000 },
    'medium':  { max_iterations: 12, max_retries: 4, max_replans: 2, token_budget: 500000 },
    'high':    { max_iterations: 24, max_retries: 5, max_replans: 3, token_budget: 2000000 }
  };
  var effort = ('' + ($.effort || 'default')).toLowerCase().trim();
  if (effort === 'med') { effort = 'medium'; }
  if (!PRESETS[effort]) { effort = 'default'; }
  var p = PRESETS[effort];

  var missing = [];
  if (!str($.objective, '')) { missing.push('objective'); }
  if (!str($.acceptance_criteria, '')) { missing.push('acceptance_criteria'); }
  if (!str($.llm_provider, '')) { missing.push('llm_provider'); }
  if (!str($.llm_model, '')) { missing.push('llm_model'); }

  var llmModel = str($.llm_model, '');
  var actor = str($.actor_workflow, 'loop_actor');
  var prePlanner = str($.pre_planner_workflow, '');
  return {
    valid: missing.length === 0 ? 'true' : 'false',
    error: missing.length === 0 ? '' : ('loop_engine: missing required input(s): ' + missing.join(', ') + '.'),
    effort: effort,
    max_iterations: clamp(num($.max_iterations, p.max_iterations), 1, 200),
    max_retries: clamp(num($.max_retries, p.max_retries), 1, 50),
    max_replans: clamp(num($.max_replans, p.max_replans), 0, 20),
    token_budget: clamp(num($.token_budget, p.token_budget), 0, 50000000),
    enable_human: flag($.enable_human, false),
    escalate_on_limit: flag($.escalate_on_limit, false),
    planner_workflow: str($.planner_workflow, 'loop_planner'),
    pre_planner_workflow: prePlanner,
    has_pre_planner: prePlanner.length > 0 ? 'true' : 'false',
    actor_workflow: actor,
    delegate_actor_workflow: str($.delegate_actor_workflow, actor),
    evaluator_workflow: str($.evaluator_workflow, 'loop_evaluator'),
    evaluator_llm_model: str($.evaluator_llm_model, llmModel)
  };
}

// --- exports (stripped by scripts/build.mjs when inlining) ---
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fn: resolveConfig, name: 'resolveConfig' };
}
