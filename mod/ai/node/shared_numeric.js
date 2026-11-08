"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const vm = require("node:vm");

const TOLERANCE = 1e-4;

function digest(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function floatHash(values) {
  if (!Array.isArray(values) || !values.every(Number.isFinite)) return null;
  const bytes = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => bytes.writeFloatLE(value, index * 4));
  return digest(bytes);
}

function same(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function errorOf(actual, expected) {
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length)
      return Infinity;
    return Math.max(
      0,
      ...expected.map((value, index) => errorOf(actual[index], value)),
    );
  }
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return Infinity;
  return Math.abs(actual - expected);
}

function comparePrediction(actual, expected, tolerance) {
  if (tolerance !== TOLERANCE)
    throw new Error("numeric tolerance must remain 1e-4");
  const logitsMaxAbsError = Math.max(
    errorOf(actual.group_logits, expected.group_logits),
    errorOf(actual.local_logits, expected.local_logits),
  );
  const valueMaxAbsError = errorOf(actual.value, expected.value);
  const probabilityMaxAbsError = errorOf(
    actual.outcome_probabilities,
    expected.outcome_probabilities,
  );
  const probabilityValid =
    Array.isArray(actual.outcome_probabilities) &&
    actual.outcome_probabilities.length === 3 &&
    actual.outcome_probabilities.every(
      (value) => Number.isFinite(value) && value >= 0 && value <= 1,
    ) &&
    Math.abs(
      actual.outcome_probabilities.reduce((sum, value) => sum + value, 0) - 1,
    ) <= TOLERANCE;
  const encoderPass =
    (expected.observation_sha256 !== undefined
      ? floatHash(actual.observation) === expected.observation_sha256
      : floatHash(actual.observation) !== null &&
        same(actual.observation, expected.observation)) &&
    ["action_features", "action_group_ids", "group_features"].every((field) =>
      same(actual[field], expected[field]),
    );
  const servedIndexDisagreement =
    !Number.isInteger(actual.chosen_index) ||
    !Number.isInteger(expected.chosen_index) ||
    actual.chosen_index !== expected.chosen_index;
  const actionDisagreement =
    typeof actual.chosen_action_id !== "string" ||
    actual.chosen_action_id !== expected.chosen_action_id;
  const actualFeature = actual.action_features?.[actual.chosen_index];
  const expectedFeature = expected.action_features?.[expected.chosen_index];
  const identicalFeatureDisagreement =
    servedIndexDisagreement &&
    Array.isArray(actualFeature) &&
    Array.isArray(expectedFeature) &&
    same(actualFeature, expectedFeature);
  const invalid =
    ![logitsMaxAbsError, valueMaxAbsError, probabilityMaxAbsError].every(
      Number.isFinite,
    ) || !probabilityValid;
  return {
    pass:
      !invalid &&
      encoderPass &&
      !servedIndexDisagreement &&
      !actionDisagreement &&
      logitsMaxAbsError <= tolerance &&
      valueMaxAbsError <= tolerance &&
      probabilityMaxAbsError <= tolerance,
    encoderPass,
    servedIndexDisagreement,
    actionDisagreement,
    identicalFeatureDisagreement,
    invalid,
    logitsMaxAbsError,
    valueMaxAbsError,
    probabilityMaxAbsError,
    tolerance,
  };
}

function classBody(source, name) {
  const declaration = new RegExp("\\bclass\\s+" + name + "\\b").exec(source);
  if (!declaration) throw new Error("missing AS3 class " + name);
  const start = source.indexOf("{", declaration.index);
  let depth = 1;
  let quote = "";
  let comment = "";
  let escaped = false;
  for (let index = start + 1; index < source.length; index++) {
    const character = source[index];
    const pair = source.slice(index, index + 2);
    if (comment) {
      if (comment === "//" && character === "\n") comment = "";
      else if (comment === "/*" && pair === "*/") {
        comment = "";
        index++;
      }
    } else if (quote) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === quote) quote = "";
    } else if (pair === "//" || pair === "/*") {
      comment = pair;
      index++;
    } else if (character === "'" || character === '"') quote = character;
    else if (character === "{") depth++;
    else if (character === "}" && --depth === 0)
      return source.slice(start + 1, index);
  }
  throw new Error("unclosed AS3 class " + name);
}

function loadPolicy(source) {
  const body = classBody(source, "GwaiNetPolicy")
    .replace(/private static var FLOAT32_BUFFER:[\s\S]*?;/g, "")
    .replace(
      /public function\s+GwaiNetPolicy\s*\(\s*\)\s*\{\s*super\(\);\s*\}/g,
      "",
    )
    .replace(/(?:public|private) static (const|var)\s+/g, "var ")
    .replace(
      /(?:public|private) static function\s+(\w+)\(([^)]*)\)\s*:\s*(?:Vector\.<Number>|Object|Array|Number|String|Boolean|int|void)\s*\{/g,
      "function $1($2) {",
    )
    .replace(/\bcatch\s*\((\w+)\s*:\s*(?:Error|Object)\s*\)/g, "catch ($1)")
    .replace(
      /:\s*(?:Vector\.<Number>|Object|Array|Number|Boolean|String|int|uint)(?=[,\s)=;])/g,
      "",
    )
    .replace(/\bint\s*\(/g, "Math.trunc(")
    .replace(
      /new Vector\.<Number>\(([^,]+),\s*true\)/g,
      "new Array($1).fill(0)",
    );
  const moduleShim = { exports: {} };
  const math = Object.create(Math);
  math.random = () => {
    throw new Error("temperature-zero numerical gate consumed RNG");
  };
  const context = {
    module: moduleShim,
    Math: math,
    FLOAT32_BUFFER: {
      position: 0,
      value: 0,
      writeFloat(value) {
        this.value = Math.fround(value);
      },
      readFloat() {
        return this.value;
      },
    },
  };
  vm.runInNewContext(
    body + "\nmodule.exports = { evaluate: evaluate };",
    context,
    { timeout: 10000 },
  );
  return moduleShim.exports;
}

function runProbeGate(
  sourcePath,
  metadataPath,
  weightsPath,
  probesPath,
  outputPath,
) {
  if (fs.existsSync(outputPath))
    throw new Error("numeric result output must be fresh");
  const started = process.hrtime.bigint();
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  const bytes = fs.readFileSync(weightsPath);
  if (
    bytes.length !== metadata.weight_bytes ||
    bytes.length !== metadata.parameter_count * 4 ||
    digest(bytes) !== metadata.weights_sha256 ||
    digest(bytes) !== metadata.export_sha256
  ) {
    throw new Error("numeric checkpoint payload identity differs");
  }
  const source = fs.readFileSync(sourcePath, "utf8");
  const probes = JSON.parse(fs.readFileSync(probesPath, "utf8"));
  const model = probes.models?.[metadata.checkpoint_sha256];
  const fictional = probes.format === "witcher3-policy-probes-v1";
  const actualPrimary =
    probes.format === "witcher3-shared-actual-primary-probes-v1";
  const expectedCount = fictional ? 200 : model?.header?.position_count;
  if (
    (!fictional && !actualPrimary) ||
    !model ||
    model.header.checkpoint_sha256 !== metadata.checkpoint_sha256 ||
    model.header.catalog_sha256 !== metadata.catalog_sha256 ||
    model.header.observation_profile_id !== metadata.observation_profile_id ||
    !Number.isInteger(expectedCount) ||
    expectedCount < 1 ||
    model.cases.length !== expectedCount ||
    (fictional && model.header.dev_game_count !== 0) ||
    (actualPrimary &&
      (model.header.counter_contract !== "completed-primary-turns-v1" ||
        !Array.isArray(model.header.source_journals) ||
        model.header.source_journals.length !== model.header.dev_game_count ||
        model.header.dev_game_count < 1)) ||
    model.header.rules_validated !== false
  )
    throw new Error("numeric probe identity differs");
  const weights = Array.from({ length: metadata.parameter_count }, (_, index) =>
    bytes.readFloatLE(index * 4),
  );
  if (!weights.every(Number.isFinite))
    throw new Error("nonfinite checkpoint payload");
  const policy = loadPolicy(source);
  const loadingSeconds = Number(process.hrtime.bigint() - started) / 1e9;
  const results = [];
  const durations = [];
  for (const record of model.cases) {
    if (
      actualPrimary &&
      (!model.header.source_journals.some(
        (journal) => journal.sha256 === record.origin?.journal_sha256,
      ) ||
        record.observation.seat !== record.origin.acting_seat ||
        record.observation.to_move !== record.origin.acting_seat ||
        record.observation.decision_id !== record.origin.primary_turn ||
        record.observation.pending_choice !== null ||
        record.observation.terminal !== null)
    ) {
      throw new Error("actual numeric input lost its actor-primary origin");
    }
    const expectedIndex = record.legal_actions.findIndex(
      (action) => action.id === record.expected.action_id,
    );
    if (
      expectedIndex < 0 ||
      record.legal_actions.filter(
        (action) => action.id === record.expected.action_id,
      ).length !== 1
    ) {
      throw new Error("probe canonical action is missing or ambiguous");
    }
    const before = process.hrtime.bigint();
    const actual = policy.evaluate(
      record.observation,
      record.legal_actions,
      model.card_ids,
      model.card_effects,
      weights,
      metadata.offsets,
      metadata.width,
      metadata.blocks,
      0,
      metadata.observation_profile_id,
    );
    durations.push(Number(process.hrtime.bigint() - before) / 1e6);
    const expected = {
      ...record.expected,
      chosen_index: expectedIndex,
      chosen_action_id: record.expected.action_id,
    };
    const result = comparePrediction(actual, expected, TOLERANCE);
    result.canonicalActionPass = same(
      actual.chosen_action,
      record.legal_actions[actual.chosen_index],
    );
    result.pass = result.pass && result.canonicalActionPass;
    results.push({
      position_id: record.position_id,
      ...result,
      actual_index: actual.chosen_index,
      expected_index: expectedIndex,
      actual_action_id: actual.chosen_action_id,
      expected_action_id: expected.chosen_action_id,
    });
  }
  const max = (field) => Math.max(...results.map((row) => row[field]));
  const output = {
    mode: "shared-policy-source-translation-numerical-gate",
    dataset_format: probes.format,
    source_sha256: digest(Buffer.from(source)),
    checkpoint_sha256: metadata.checkpoint_sha256,
    weights_sha256: metadata.weights_sha256,
    probes_sha256: digest(fs.readFileSync(probesPath)),
    observation_profile_id: metadata.observation_profile_id,
    positions: results.length,
    pass: results.length === expectedCount && results.every((row) => row.pass),
    logitsMaxAbsError: max("logitsMaxAbsError"),
    valueMaxAbsError: max("valueMaxAbsError"),
    probabilityMaxAbsError: max("probabilityMaxAbsError"),
    probabilityMetric: "critic-loss-draw-win-only",
    policyPriorCompared: false,
    servedIndexDisagreements: results.filter(
      (row) => row.servedIndexDisagreement,
    ).length,
    identicalFeatureDisagreements: results.filter(
      (row) => row.identicalFeatureDisagreement,
    ).length,
    invalidPositions: results.filter((row) => row.invalid).length,
    encoderMismatches: results.filter((row) => !row.encoderPass).length,
    tolerance: TOLERANCE,
    loadingSeconds,
    translatedDecisionMilliseconds: {
      count: durations.length,
      first: durations[0],
      max: Math.max(...durations),
      values: durations,
    },
    playerLatencyEvidence: false,
    vmExecutionEvidence: false,
    nativeVerified: false,
    strengthEvidence: false,
    results,
  };
  fs.writeFileSync(outputPath, JSON.stringify(output, null, 2) + "\n", {
    flag: "wx",
  });
  return output;
}

if (require.main === module) {
  const [source, metadata, weights, probes, output] = process.argv.slice(2);
  if (!output || process.argv.length !== 7)
    throw new Error(
      "usage: shared_numeric.js SOURCE METADATA WEIGHTS PROBES RESULT",
    );
  const result = runProbeGate(source, metadata, weights, probes, output);
  process.stdout.write(
    JSON.stringify({
      pass: result.pass,
      positions: result.positions,
      logitsMaxAbsError: result.logitsMaxAbsError,
      valueMaxAbsError: result.valueMaxAbsError,
      probabilityMaxAbsError: result.probabilityMaxAbsError,
      servedIndexDisagreements: result.servedIndexDisagreements,
    }) + "\n",
  );
  if (!result.pass) process.exitCode = 1;
}

module.exports = { classBody, comparePrediction, loadPolicy, runProbeGate };
