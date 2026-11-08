"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");

function prediction() {
  return {
    chosen_index: 0,
    chosen_action_id: "first",
    observation: [0.25, 1],
    group_logits: [0.1, 0.1],
    local_logits: [0.2, 0.2],
    action_features: [
      [1, 2],
      [1, 2],
    ],
    action_group_ids: [0, 1],
    group_features: [
      [1, 2],
      [1, 2],
    ],
    value: 0.4,
    outcome_probabilities: [0.2, 0.2, 0.6],
  };
}

test("policy and value comparison retains errors separately", () => {
  const { comparePrediction } = require("../mod/ai/node/shared_numeric.js");
  const expected = prediction();
  const actual = prediction();
  actual.local_logits[0] += 1e-6;
  actual.value += 2e-6;
  actual.outcome_probabilities[0] += 3e-6;
  const result = comparePrediction(actual, expected, 1e-4);
  assert.equal(result.pass, true);
  assert.ok(result.logitsMaxAbsError > 0);
  assert.ok(result.valueMaxAbsError > result.logitsMaxAbsError);
  assert.ok(result.probabilityMaxAbsError > result.valueMaxAbsError);
});

test("served index disagreement fails despite duplicate identical features", () => {
  const { comparePrediction } = require("../mod/ai/node/shared_numeric.js");
  const expected = prediction();
  const actual = prediction();
  actual.chosen_index = 1;
  actual.chosen_action_id = "second";
  const result = comparePrediction(actual, expected, 1e-4);
  assert.equal(result.pass, false);
  assert.equal(result.servedIndexDisagreement, true);
  assert.equal(result.identicalFeatureDisagreement, true);
});

test("NaN, missing value and wrong logit count cannot pass", () => {
  const { comparePrediction } = require("../mod/ai/node/shared_numeric.js");
  for (const mutate of [
    (p) => {
      p.value = NaN;
    },
    (p) => {
      delete p.value;
    },
    (p) => {
      p.group_logits.push(0);
    },
    (p) => {
      p.outcome_probabilities = [0, 1];
    },
  ]) {
    const actual = prediction();
    mutate(actual);
    assert.equal(comparePrediction(actual, prediction(), 1e-4).pass, false);
  }
});

test("encoder or probability mismatch fails at the fixed tolerance", () => {
  const { comparePrediction } = require("../mod/ai/node/shared_numeric.js");
  const actual = prediction();
  actual.observation[0] += 1e-6;
  actual.outcome_probabilities[2] += 0.001;
  const result = comparePrediction(actual, prediction(), 1e-4);
  assert.equal(result.pass, false);
  assert.equal(result.encoderPass, false);
  assert.ok(result.probabilityMaxAbsError > 1e-4);
});
