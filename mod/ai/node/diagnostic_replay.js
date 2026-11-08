#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const { loadNetworkPolicy } = require("./replay.js");
const LOGIT_TOLERANCE = 1e-4;
const ENCODER_TOLERANCE = 1e-7;
function sha(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}
function json(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  return value;
}
function equal(actual, expected, label) {
  assert.equal(JSON.stringify(canonical(actual)), JSON.stringify(canonical(expected)), label);
}
function maxError(actual, expected, label) {
  assert.equal(actual.length, expected.length, `${label} length`);
  let error = 0;
  for (let index = 0; index < actual.length; index++) {
    assert.ok(
      Number.isFinite(actual[index]) && Number.isFinite(expected[index]),
      `${label} finite`,
    );
    error = Math.max(error, Math.abs(actual[index] - expected[index]));
  }
  return error;
}
function floatHash(values) {
  const bytes = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => bytes.writeFloatLE(value, index * 4));
  return sha(bytes);
}
function replay(options, progress = { cases: [], current: null }) {
  const buildBytes = fs.readFileSync(options.buildManifest);
  const manifest = JSON.parse(buildBytes);
  assert.equal(manifest.variant, "diagnostic");
  assert.equal(manifest.status, "offline_compiled_and_export_verified");
  assert.equal(manifest.policy_diagnostic.schema, "w3gwent-policy-diagnostic-v1");
  const inputs = json(options.inputs);
  assert.equal(inputs.schema, "w3gwent-policy-diagnostic-v1");
  assert.equal(inputs.build_identity, manifest.policy_diagnostic.build_identity);
  assert.equal(inputs.build_manifest_sha256, sha(buildBytes), "exact diagnostic manifest");
  assert.equal(
    inputs.complete,
    true,
    "incomplete/failed capture cannot claim successful full replay",
  );
  assert.equal(
    inputs.failed_attempts,
    0,
    "failed policy attempts require diagnosis, not successful replay",
  );
  assert.ok(
    Array.isArray(inputs.variants) && inputs.variants.length > 0 && inputs.variants.length <= 512,
  );
  const network = fs.readFileSync(options.network);
  const core = fs.readFileSync(options.core);
  assert.equal(
    sha(network),
    manifest.policy_network_export_sha256,
    "compiled diagnostic network identity",
  );
  assert.equal(sha(core), manifest.policy_core_export_sha256, "compiled policy core identity");
  const models = new Map();
  for (const directory of options.models) {
    const metadata = json(path.join(directory, "metadata.json"));
    const bytes = fs.readFileSync(path.join(directory, "policy.f32.bin"));
    assert.equal(sha(bytes), metadata.export_sha256, "weight bytes identity");
    assert.equal(bytes.length, metadata.parameter_count * 4);
    assert.ok(
      metadata.observation_profile_id === "full-faction-v1" ||
        metadata.observation_profile_id === "faction-unknown-v1",
    );
    assert.ok(!models.has(metadata.checkpoint_sha256), "duplicate model binding");
    const weights = Array.from({ length: metadata.parameter_count }, (_, index) =>
      bytes.readFloatLE(index * 4),
    );
    assert.ok(weights.every(Number.isFinite));
    models.set(metadata.checkpoint_sha256, { metadata, weights });
  }
  const cases = progress.cases;
  const ids = new Set();
  for (const variant of inputs.variants) {
    progress.current = variant.id;
    assert.ok(
      typeof variant.id === "string" && !ids.has(variant.id),
      "unique captured decision identity",
    );
    ids.add(variant.id);
    const expected = variant.expected;
    const tier = ["easy", "normal", "hard"][expected.difficulty - 1];
    assert.ok(tier && Number.isInteger(expected.difficulty));
    const declared = manifest.tier_policies[tier];
    for (const [field, value] of Object.entries({
      checkpoint_sha256: declared.checkpoint_sha256,
      weights_sha256: declared.export_sha256,
      observation_profile_id: declared.observation_profile_id,
      temperature: declared.temperature,
    }))
      assert.equal(expected[field], value, field);
    const model = models.get(expected.checkpoint_sha256);
    assert.ok(model, "missing exact model binding");
    assert.equal(model.metadata.export_sha256, expected.weights_sha256);
    assert.equal(model.metadata.observation_profile_id, expected.observation_profile_id);
    assert.equal(model.metadata.observation_size, declared.observation_size);
    assert.equal(model.metadata.card_count, declared.card_count);
    equal(variant.observation, expected.observation, "collector observation equality");
    equal(variant.legal_actions, expected.legal_actions, "collector legal equality");
    const draws = expected.random_draws;
    assert.ok(
      Array.isArray(draws) &&
        draws.every((value) => Number.isFinite(value) && value >= 0 && value < 1),
    );
    let cursor = 0;
    let begins = 0;
    let records = 0;
    let actual = null;
    let recordedObservation = null;
    let recordedLegal = null;
    const random = () => {
      assert.ok(cursor < draws.length, "missing recorded policy random draw");
      return draws[cursor++];
    };
    const hooks = {
      beginDecision() {
        begins++;
        assert.equal(begins, 1);
      },
      random,
      failedAttempt() {
        throw new Error("network inference failed during diagnostic replay");
      },
      record(observation, legal, result, difficulty, data, temperature) {
        records++;
        actual = result;
        recordedObservation = observation;
        recordedLegal = legal;
        assert.equal(difficulty, expected.difficulty);
        assert.equal(temperature, expected.temperature);
        assert.equal(data.CHECKPOINT_SHA256, expected.checkpoint_sha256);
      },
    };
    const policy = loadNetworkPolicy(
      network.toString("utf8"),
      core.toString("utf8"),
      model.metadata,
      model.weights,
      random,
      expected.temperature,
      false,
      hooks,
    );
    const observation = structuredClone(variant.observation);
    const legal = structuredClone(variant.legal_actions);
    const view = {
      policyMode: "network",
      observation,
      playerID: observation.seat,
      opponentID: 1 - observation.seat,
      scores: observation.scores,
    };
    const selected = policy.decide(view, legal, expected.difficulty);
    assert.notEqual(view.policyFallback, true, "served replay fallback");
    assert.equal(begins, 1, "diagnostic begin hook");
    assert.equal(records, 1, "one captured result from one served forward");
    assert.equal(cursor, draws.length, "extra recorded policy random draws");
    equal(recordedObservation, variant.observation, "policy observation was not mutated");
    equal(recordedLegal, variant.legal_actions, "policy legal list was not mutated");
    assert.equal(actual.observation_profile_id, expected.observation_profile_id);
    assert.equal(actual.chosen_index, expected.selected_index, "sampled local choice");
    assert.equal(
      policy.actionId(selected),
      expected.selected_action_id,
      "actual served sampled choice",
    );
    equal(selected, expected.selected_action, "selected full transaction");
    for (const field of ["action_features", "group_features", "action_group_ids"])
      equal(actual[field], expected[field], field);
    const encoded = Array.from(actual.observation);
    const encoderError = maxError(encoded, expected.encoded_observation, "encoder");
    const logitError = Math.max(
      maxError(actual.group_logits, expected.group_logits, "group logits"),
      maxError(actual.local_logits, expected.local_logits, "local logits"),
    );
    assert.ok(encoderError <= ENCODER_TOLERANCE, "encoder tolerance");
    assert.ok(logitError <= LOGIT_TOLERANCE, "logit tolerance");
    let valueError = 0;
    if (expected.value_nonfinite) {
      assert.ok(!Number.isFinite(actual.value));
      assert.equal(actual.outcome_probabilities, null);
    } else {
      valueError = Math.max(
        Math.abs(actual.value - expected.value),
        maxError(actual.outcome_probabilities, expected.outcome_probabilities, "outcomes"),
      );
      assert.ok(valueError <= LOGIT_TOLERANCE, "value tolerance");
    }
    cases.push({
      id: variant.id,
      pass: true,
      random_draws_consumed: cursor,
      selected_action_id: policy.actionId(selected),
      encoder_max_abs_error: encoderError,
      logits_max_abs_error: logitError,
      value_max_abs_error: valueError,
      encoded_float32_sha256: floatHash(encoded),
      captured_float32_sha256: floatHash(expected.encoded_observation),
    });
  }
  return {
    kind: "witcher3_policy_diagnostic_replay",
    pass: true,
    positions: cases.length,
    build_identity: inputs.build_identity,
    build_manifest_sha256: sha(buildBytes),
    information: "offline acting-seat capture replay; no opponent hidden cards",
    cases,
  };
}
function main() {
  const options = { models: [] };
  const keys = {
    "--build-manifest": "buildManifest",
    "--inputs": "inputs",
    "--network": "network",
    "--core": "core",
    "--output": "output",
  };
  for (let index = 2; index < process.argv.length; index++) {
    const flag = process.argv[index];
    assert.ok(index + 1 < process.argv.length, "missing CLI value");
    const value = process.argv[++index];
    if (flag === "--model") options.models.push(path.resolve(value));
    else {
      assert.ok(
        Object.hasOwn(keys, flag) && !Object.hasOwn(options, keys[flag]),
        "unknown/duplicate option",
      );
      options[keys[flag]] = path.resolve(value);
    }
  }
  assert.ok(Object.values(keys).every((key) => options[key]) && options.models.length > 0);
  assert.ok(!fs.existsSync(options.output), "fresh result file required");
  let result;
  const progress = { cases: [], current: null };
  try {
    result = replay(options, progress);
  } catch (error) {
    result = {
      kind: "witcher3_policy_diagnostic_replay",
      pass: false,
      error: error.message,
      failed_case: progress.current,
      completed_cases: progress.cases,
    };
    process.exitCode = 1;
  }
  fs.writeFileSync(options.output, `${JSON.stringify(result, null, 2)}\n`, { flag: "wx" });
  process.stdout.write(
    `${JSON.stringify({ kind: result.kind, pass: result.pass, positions: result.positions, error: result.error })}\n`,
  );
}
if (require.main === module) main();
module.exports = { replay };
