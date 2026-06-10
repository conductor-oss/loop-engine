// Canonical decision policy for the loop_engine `decide` task.
//
// THIS FILE IS THE SOURCE OF TRUTH. `node scripts/build.mjs` inlines it into
// workflows/loop_engine.json; `node scripts/build.mjs --check` (run by the test
// suite) fails if the JSON has drifted. Keep this graaljs-compatible: ES5 only
// (no let/const/arrow/template literals).
//
// Inputs ($) — every field may arrive as a string, number, boolean, or null:
//   passed, score, recommend           evaluator verdict (normalized by eval_guard)
//   act_ok, eval_ok                    infra health of this iteration's sub-workflows
//   fail_streak, infra_streak          persisted counters (quality vs infrastructure)
//   best_score, replans_used           persisted progress state
//   spent_tokens, act_tokens, eval_tokens, token_budget
//   max_retries, max_replans, enable_human, escalate_on_limit
//
// Output: { decision, status, fail_streak, infra_streak, replans_used,
//           spent_tokens, reason }
//
// Priority order (first match wins):
//   1. accept       — verdict passed on a healthy iteration
//   2. budget       — hard token ceiling (stop, or escalate when configured)
//   3. infra        — actor/evaluator sub-workflow failed: bounded infra retries;
//                     never replans (a new strategy cannot fix a failing provider)
//   4. delegate     — evaluator recommends switching to the alternate actor
//   5. progress     — score improved beyond threshold: retry with feedback
//   6. no progress  — bounded retries, then replan, then escalate/stop

function decide($) {
  function num(v, d) { var n = Number(v); return isNaN(n) ? d : n; }
  function flag(v) { return v === true || v === 'true'; }

  var actOk = ($.act_ok === null || $.act_ok === undefined) ? true : flag($.act_ok);
  var evalOk = ($.eval_ok === null || $.eval_ok === undefined) ? true : flag($.eval_ok);
  var passed = flag($.passed) && actOk && evalOk;
  var score = num($.score, 0);
  var best = num($.best_score, 0);
  var fs = num($.fail_streak, 0);
  var infra = num($.infra_streak, 0);
  var ru = num($.replans_used, 0);
  var spent = num($.spent_tokens, 0) + num($.act_tokens, 0) + num($.eval_tokens, 0);
  var budget = num($.token_budget, 0);
  var maxRetries = num($.max_retries, 3);
  var maxReplans = num($.max_replans, 1);
  var human = flag($.enable_human);
  var escLimit = flag($.escalate_on_limit);
  var rec = '' + ($.recommend || '');

  var decision = 'retry';
  var status = 'running';
  var reason = '';

  if (passed) {
    decision = 'accept'; status = 'succeeded';
    reason = 'Acceptance criteria satisfied.';
  } else if (budget > 0 && spent >= budget) {
    if (escLimit && human) {
      decision = 'escalate'; status = 'escalated';
      reason = 'Token budget exhausted (' + spent + '/' + budget + '); escalating to human.';
    } else {
      decision = 'stop'; status = 'stopped_budget';
      reason = 'Token budget exhausted (' + spent + '/' + budget + ').';
    }
  } else if (!actOk || !evalOk) {
    infra = infra + 1;
    var part = !actOk ? 'Actor' : 'Evaluator';
    if (infra >= maxRetries) {
      if (human) {
        decision = 'escalate'; status = 'escalated';
        reason = part + ' sub-workflow failed ' + infra + ' consecutive times; escalating to human.';
      } else {
        decision = 'stop'; status = 'stopped_infra_failure';
        reason = part + ' sub-workflow failed ' + infra + ' consecutive times; stopping.';
      }
    } else {
      decision = 'retry';
      reason = part + ' sub-workflow failed (infra streak ' + infra + '/' + maxRetries + '); retrying the iteration.';
    }
  } else if (rec === 'delegate') {
    decision = 'delegate'; fs = 0; infra = 0;
    reason = 'Evaluator recommended delegating to the alternate actor.';
  } else {
    infra = 0;
    if (score > best + 0.01) {
      decision = 'retry'; fs = 0;
      reason = 'Score improved (' + best + ' -> ' + score + '); retrying with feedback.';
    } else {
      fs = fs + 1;
      if (fs >= maxRetries) {
        if (ru < maxReplans) {
          decision = 'replan'; ru = ru + 1; fs = 0;
          reason = 'No progress after ' + maxRetries + ' tries; replanning (' + ru + '/' + maxReplans + ').';
        } else if (human) {
          decision = 'escalate'; status = 'escalated';
          reason = 'No progress and replans exhausted; escalating to human.';
        } else {
          decision = 'stop'; status = 'stopped_no_progress';
          reason = 'No progress and replans exhausted; stopping.';
        }
      } else {
        decision = 'retry';
        reason = 'Not passing yet (fail streak ' + fs + '/' + maxRetries + '); retrying with feedback.';
      }
    }
  }

  return { decision: decision, status: status, fail_streak: fs, infra_streak: infra,
           replans_used: ru, spent_tokens: spent, reason: reason };
}

// --- exports (stripped by scripts/build.mjs when inlining) ---
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fn: decide, name: 'decide' };
}
