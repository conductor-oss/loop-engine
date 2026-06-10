// Canonical verdict normalization for the loop_engine `eval_guard` task.
//
// THIS FILE IS THE SOURCE OF TRUTH — see scripts/build.mjs. ES5 only (graaljs).
//
// Sits between the (optional, may-have-failed) evaluator sub-workflow and the
// decision policy. It produces exactly one normalized verdict per iteration:
//   * act_ok / eval_ok      — infra health flags consumed by `decide`
//   * passed / score        — coerced; forced to false/0 on any infra failure so
//                             a failed iteration can never be accepted or become
//                             the "best" result
//   * feedback              — composed (infra failures get explicit INFRA feedback)
//                             and TRUNCATED to a hard cap so workflow-variable
//                             state stays bounded no matter what an evaluator emits
//   * tokens                — coerced to a number for budget accounting
//
// The evaluator is considered healthy iff it produced ANY verdict field; a
// custom evaluator that returns all-null output is treated as an infra failure,
// not as a (dangerously meaningless) failing-score verdict.

function evalGuard($) {
  var FEEDBACK_MAX = 2000;
  function flag(v) { return v === true || v === 'true'; }
  function num(v, d) { var n = Number(v); return isNaN(n) ? d : n; }
  function isNil(v) { return v === null || v === undefined; }

  var actOk = isNil($.act_ok) ? true : flag($.act_ok);
  var evalOk = !isNil($.passed) || !isNil($.score) ||
               (typeof $.feedback === 'string' && $.feedback.length > 0);

  var passed = flag($.passed);
  var score = num($.score, 0);
  if (score < 0) { score = 0; }
  var feedback = isNil($.feedback) ? '' : ('' + $.feedback);
  var recommend = '' + ($.recommend || '');
  var tokens = num($.tokens, 0);

  if (!actOk) {
    passed = false; score = 0; recommend = '';
    var err = (typeof $.act_error === 'string' && $.act_error.length > 0) ? (' ' + $.act_error) : '';
    feedback = 'INFRA: the actor sub-workflow failed to produce a result; this attempt never executed.' + err;
  } else if (!evalOk) {
    passed = false; score = 0; recommend = '';
    feedback = 'INFRA: the evaluator sub-workflow returned no verdict; this attempt cannot be judged.';
  }

  if (feedback.length > FEEDBACK_MAX) {
    feedback = feedback.substring(0, FEEDBACK_MAX) + ' ...[truncated]';
  }

  return { act_ok: actOk, eval_ok: evalOk, passed: passed, score: score,
           feedback: feedback, recommend: recommend, tokens: tokens };
}

// --- exports (stripped by scripts/build.mjs when inlining) ---
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fn: evalGuard, name: 'evalGuard' };
}
