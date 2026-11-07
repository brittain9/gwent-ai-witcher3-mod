#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const vm = require("node:vm");

function classBody(source, className = "GwaiPolicyCore") {
  const start = source.indexOf(`internal class ${className}`);
  if (start < 0) throw new Error(`${className} class declaration is missing`);
  const open = source.indexOf("{", start);
  if (open < 0) throw new Error(`${className} class body is missing`);
  let depth = 1;
  for (let index = open + 1; index < source.length; index++) {
    if (source[index] === "{") depth++;
    if (source[index] === "}") depth--;
    if (depth === 0) return source.slice(open + 1, index);
  }
  throw new Error(`${className} class body is unterminated`);
}

function loadPolicyCore(corePath) {
  const bytes = corePath === "-" ? fs.readFileSync(0) : fs.readFileSync(corePath);
  const source = bytes.toString("utf8");
  const digest = crypto.createHash("sha256").update(bytes).digest("hex");
  const body = classBody(source).replace(/public function\s+GwaiPolicyCore\s*\(\s*\)\s*\{\s*super\(\);\s*\}/, "");

  const javascript = body
    .replace(/public static function\s+(\w+)\(([^)]*)\)\s*:\s*(?:Object|Number|Boolean|int)\s*\{/g, "function $1($2) {")
    .replace(/\bcatch\s*\((\w+)\s*:\s*(?:Error|Object)\s*\)/g, "catch ($1)")
    .replace(/\b([A-Za-z_$][\w$]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b/g, "$1")
    .replace(/\bvar\s+([A-Za-z_$][\w$]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b/g, "var $1");
  const moduleShim = { exports: {} };
  vm.runInNewContext(`${javascript}\nmodule.exports = { decide: decide };`, { module: moduleShim });
  return { decide: moduleShim.exports.decide, digest };
}

function moveKey(move) {
  if (!move || move.kind === "pass") return "pass";
  return `${move.kind}:${move.instanceId}`;
}

function replay(inputPath, decide, limit) {
  const lines = fs.readFileSync(inputPath, "utf8").split(/\r?\n/).filter(Boolean)
    .map((line) => JSON.parse(line)).filter((record) => !record.fixture_schema);
  let agreement = 0;
  for (let index = 0; index < lines.length; index++) {
    const record = lines[index];
    const legal = limit == null ? record.legal : record.legal.slice(0, limit);
    const actual = decide(record.view, legal);
    if (moveKey(actual) === moveKey(record.expected)) agreement++;
  }
  return { agreement, total: lines.length };
}

function loadNetworkPolicy(source, coreSource, metadata, weights, random = Math.random, easyTemperature = 0, probeTiming = false) {
  const networkBody = classBody(source, "GwaiNetPolicy")
    .replace(/private static (?:const|var)\s+\w+:[\s\S]*?;/g, "")
    .replace(/public function\s+GwaiNetPolicy\s*\(\s*\)\s*\{\s*super\(\);\s*\}/, "")
    .replace(/(?:public|private) static function\s+(\w+)\(([^)]*)\)\s*:\s*(?:Object|Array|Number|String|Boolean|int|void|Vector\.<Number>)\s*\{/g, "function $1($2) {")
    .replace(/\bcatch\s*\((\w+)\s*:\s*(?:Error|Object)\s*\)/g, "catch ($1)")
    .replace(/:\s*(?:Vector\.<Number>|Object|Array|Number|Boolean|String|int|uint)(?=[,\s)=;])/g, "")
    .replace(/\bint\s*\(/g, "Math.trunc(")
    .replace(/new Vector\.<Number>\(([^,]+),\s*true\)/g, "new Array($1).fill(0)");
  const networkWithClassName = networkBody.replace(/\bfunction decide\(/, "function networkDecide(");
  const coreBody = classBody(coreSource, "GwaiPolicyCore")
    .replace(/public function\s+GwaiPolicyCore\s*\(\s*\)\s*\{\s*super\(\);\s*\}/, "")
    .replace(/public static function\s+(\w+)\(([^)]*)\)\s*:\s*(?:Object|Number|Boolean|int)\s*\{/g, (_match, name, args) => "function " + name + "(" + args + ") {")
    .replace(/catch\s*\((\w+)\s*:\s*(?:Error|Object)\s*\)/g, (_match, name) => "catch (" + name + ")")
    .replace(/\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b/g, "$1")
    .replace(/\bvar\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b/g, "var $1");
  const weightBytes = Buffer.alloc(weights.length * 4);
  for (let index = 0; index < weights.length; index++) weightBytes.writeFloatLE(weights[index], index * 4);
  const encodedWeights = weightBytes.toString("base64");
  const data = {
    EMBEDDED: true,
    CHECKPOINT_SHA256: metadata.checkpoint_sha256,
    UPDATE_COUNT: metadata.update_count,
    EXPORT_SHA256: metadata.export_sha256,
    CATALOG_SHA256: metadata.catalog_sha256,
    WEIGHT_COUNT: metadata.parameter_count,
    OBSERVATION_SIZE: metadata.observation_size,
    CARD_COUNT: metadata.card_count,
    WIDTH: metadata.width,
    BLOCKS: metadata.blocks,
    OFFSETS: metadata.offsets,
    CARD_IDS: metadata.card_ids,
    CARD_EFFECTS: metadata.card_effects,
    BASE64: Array.from({ length: Math.ceil(encodedWeights.length / 48000) }, (_, index) =>
      encodedWeights.slice(index * 48000, (index + 1) * 48000)),
  };
  let weightLoadCalls = 0;
  let loadedWeights = null;
  const math = Object.create(Math);
  math.random = random;
  const timing = createTimingProbe(probeTiming);
  const float32Buffer = {
    value: 0,
    position: 0,
    writeFloat(value) { this.value = Math.fround(value); },
    readFloat() { return this.value; },
  };
  const moduleShim = { exports: {} };
  const context = {
    module: moduleShim,
    Math: math,
    getTimer: () => Number(process.hrtime.bigint()) / 1e6,
    GwaiTimingProbe: timing,
    FLOAT32_BUFFER: float32Buffer,
    Number,
    String,
    Boolean,
    Error,
    GwaiNetWeightsData: { OBSERVATION_SIZE: metadata.observation_size },
    GwaiNetTierConfig: {
      dataFor(difficulty) {
        if (difficulty < 1 || difficulty > 3) throw new Error("difficulty outside 1..3");
        return data;
      },
      temperatureFor(difficulty) {
        if (difficulty < 1 || difficulty > 3) throw new Error("difficulty outside 1..3");
        return difficulty === 1 ? easyTemperature : 0;
      },
    },
    GwaiNetWeights: {
      load(requestedData) {
        weightLoadCalls++;
        if (requestedData !== data || !requestedData.EMBEDDED || !Array.isArray(requestedData.BASE64) ||
          requestedData.BASE64.length === 0 || requestedData.WEIGHT_COUNT !== weights.length ||
          requestedData.CHECKPOINT_SHA256 !== metadata.checkpoint_sha256) {
          throw new Error("invalid embedded checkpoint data");
        }
        if (loadedWeights != null) return loadedWeights;
        const embeddedBytes = Buffer.from(requestedData.BASE64.join(""), "base64");
        const embeddedDigest = crypto.createHash("sha256").update(embeddedBytes).digest("hex");
        if (embeddedBytes.length !== requestedData.WEIGHT_COUNT * 4 ||
          embeddedDigest !== requestedData.EXPORT_SHA256) {
          throw new Error("embedded checkpoint bytes do not match their header");
        }
        loadedWeights = weights;
        return loadedWeights;
      },
    },
    GwaiNetPolicy: {},
    ROWS: ["melee", "ranged", "siege"],
    ACTION_KINDS: ["pass", "play", "leader", "mulligan", "decline_mulligan", "choice"],
    ZONE_NAMES: [
      "own_hand", "own_melee", "own_ranged", "own_siege",
      "opponent_melee", "opponent_ranged", "opponent_siege",
      "own_graveyard", "opponent_graveyard", "weather", "own_leader", "opponent_leader",
      "own_melee_modifier", "own_ranged_modifier", "own_siege_modifier",
      "opponent_melee_modifier", "opponent_ranged_modifier", "opponent_siege_modifier",
    ],
    PENDING_KINDS: [null, "mulligan", "nurse", "leader_resurrect", "leader_weather", "leader_discard", "leader_pick", "coin_toss"],
    EFFECT_COUNT: 45,
    GLOBAL_SIZE: 64,
    _lastWinChance: NaN,
  };
  vm.runInNewContext(
    networkWithClassName +
      "\nGwaiNetPolicy.decide = networkDecide;" +
      "\nGwaiNetPolicy.evaluate = evaluate;" +
      "\nGwaiNetPolicy.encodeObservation = encodeObservation;" +
      "\nGwaiNetPolicy.actionId = actionId;" +
      "\nGwaiNetPolicy.sampleLogit = sampleLogit;" +
      "\n" + coreBody +
      "\nmodule.exports = { decide: decide, evaluate: evaluate, sampleLogit: sampleLogit," +
      " actionId: GwaiNetPolicy.actionId, encodeObservation: encodeObservation," +
      " insight: insight, lastWinChance: lastWinChance, unknownCards: unknownCards, actionFeature: actionFeature };",
    context,
  );
  timing.attach(moduleShim.exports);
  return {
    ...moduleShim.exports,
    weightLoadCalls: () => weightLoadCalls,
    embeddedData: data,
    resetRandom: (seed) => { math.random = seededRandom(seed); },
    timing: () => timing.summary(metadata.width),
  };
}

function createTimingProbe(enabled) {
  const widths = [192, 128, 96, 64];
  const samples = { encoder: [], full: new Array(), 192: [], 128: [], 96: [], 64: [] };
  const models = new Map();
  let randomState = 0x4c6f6f70;
  let total = 0;
  let policy = null;

  function nextUnit() {
    randomState ^= randomState << 13;
    randomState >>>= 0;
    randomState ^= randomState >>> 17;
    randomState >>>= 0;
    randomState ^= randomState << 5;
    randomState >>>= 0;
    return randomState / 0x100000000;
  }

  function modelFor(width, data) {
    const key = `${width}:${data.OBSERVATION_SIZE}:${data.CARD_COUNT}:${data.BLOCKS}`;
    if (models.has(key)) return models.get(key);
    let count = width * data.OBSERVATION_SIZE + 3 * width;
    count += data.BLOCKS * (2 * width * width + 4 * width);
    count += 6 * 24 + (data.CARD_COUNT + 1) * 48 + 4 * 8;
    count += width * 176 + width + width * 72 + width;
    count += 2 * (256 * width + 257) + (128 * width + 515);
    const result = { weights: new Array(count).fill(0), offsets: {}, cursor: 0 };
    function appendRandom(name, size, scale) {
      result.offsets[name] = result.cursor;
      for (let index = 0; index < size; index++) {
        result.weights[result.cursor++] = (nextUnit() * 2 - 1) * scale;
      }
    }
    function appendConstant(name, size, value) {
      result.offsets[name] = result.cursor;
      for (let index = 0; index < size; index++) result.weights[result.cursor++] = value;
    }
    function appendLinear(name, outputSize, inputSize) {
      const scale = 1 / Math.sqrt(inputSize);
      appendRandom(`${name}.weight`, outputSize * inputSize, scale);
      appendRandom(`${name}.bias`, outputSize, scale);
    }
    function appendNorm(name, size) {
      appendConstant(`${name}.weight`, size, 1);
      appendConstant(`${name}.bias`, size, 0);
    }
    appendLinear("input", width, data.OBSERVATION_SIZE);
    appendNorm("input_norm", width);
    for (let block = 0; block < data.BLOCKS; block++) {
      const prefix = `blocks.${block}.`;
      appendNorm(`${prefix}norm`, width);
      appendLinear(`${prefix}first`, width, width);
      appendLinear(`${prefix}second`, width, width);
    }
    appendRandom("kind_embedding.weight", 6 * 24, 0.05);
    appendRandom("card_embedding.weight", (data.CARD_COUNT + 1) * 48, 0.05);
    appendRandom("row_embedding.weight", 4 * 8, 0.05);
    appendLinear("action_projection", width, 176);
    appendLinear("group_projection", width, 72);
    appendLinear("local_actor.0", 128, 2 * width);
    appendLinear("local_actor.2", 1, 128);
    appendLinear("group_actor.0", 128, 2 * width);
    appendLinear("group_actor.2", 1, 128);
    appendLinear("value_head.0", 128, width);
    appendLinear("value_head.2", 3, 128);
    if (result.cursor !== count) throw new Error("student weight layout size mismatch");
    models.set(key, result);
    return result;
  }

  function elapsed(start) {
    return Number(process.hrtime.bigint() - start) / 1e6;
  }

  function metric(values) {
    const sorted = values.slice().sort((left, right) => left - right);
    const mean = sorted.length === 0
      ? null
      : sorted.reduce((sum, value) => sum + value, 0) / sorted.length;
    if (sorted.length === 0) return { samples: 0, mean_ms: mean, median_ms: null, p95_ms: null };
    const middle = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 === 0
      ? (sorted[middle - 1] + sorted[middle]) / 2
      : sorted[middle];
    const p95 = sorted[Math.max(0, Math.ceil(0.95 * sorted.length) - 1)];
    return { samples: sorted.length, mean_ms: mean, median_ms: median, p95_ms: p95 };
  }

  return {
    attach(policyFunctions) { policy = policyFunctions; },
    measure(observation, legalActions, data, _realWeights, fullElapsed) {
      if (!enabled) return;
      if (policy == null) throw new Error("probe policy functions are not attached");
      for (const width of widths) modelFor(width, data);
      let started = process.hrtime.bigint();
      policy.encodeObservation(observation, data.CARD_IDS, data.CARD_EFFECTS);
      samples.encoder.push(elapsed(started));
      samples.full.push(Number(fullElapsed));
      for (const width of widths) {
        const student = modelFor(width, data);
        started = process.hrtime.bigint();
        policy.evaluate(
          observation,
          legalActions,
          data.CARD_IDS,
          data.CARD_EFFECTS,
          student.weights,
          student.offsets,
          width,
          data.BLOCKS,
          0,
        );
        samples[String(width)].push(elapsed(started));
      }
      total++;
    },
    summary(fullWidth) {
      if (!enabled) return null;
      return {
        total,
        encoder: metric(samples.encoder),
        widths: {
          full: { width: fullWidth, weights: "checkpoint", ...metric(samples.full) },
          192: { weights: "random_student", ...metric(samples["192"]) },
          128: { weights: "random_student", ...metric(samples["128"]) },
          96: { weights: "random_student", ...metric(samples["96"]) },
          64: { weights: "random_student", ...metric(samples["64"]) },
        },
      };
    },
  };
}

function seededRandom(seed) {
  let state = seed >>> 0;
  return function next() {
    state ^= state << 13;
    state >>>= 0;
    state ^= state >>> 17;
    state >>>= 0;
    state ^= state << 5;
    state >>>= 0;
    return state / 0x100000000;
  };
}

function readWeights(weightsPath, parameterCount) {
  const bytes = fs.readFileSync(weightsPath);
  if (bytes.length !== parameterCount * 4) {
    throw new Error(`weight blob has ${bytes.length} bytes; expected ${parameterCount * 4}`);
  }
  const weights = new Array(parameterCount);
  for (let index = 0; index < parameterCount; index++) weights[index] = bytes.readFloatLE(index * 4);
  return weights;
}

function float32Hash(values) {
  const bytes = Buffer.allocUnsafe(values.length * 4);
  for (let index = 0; index < values.length; index++) bytes.writeFloatLE(Number(values[index]), index * 4);
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function replayNetwork(inputPath, networkPath, corePath, metadataPath, weightsPath, tolerance, expectedDigest, positionLimit, samplerSeed, samplerDraws, easyTemperature, probeTiming, actionSequenceHash) {
  const networkBytes = networkPath === "-" ? fs.readFileSync(0) : fs.readFileSync(networkPath);
  const networkSource = networkBytes.toString("utf8");
  const networkDigest = crypto.createHash("sha256").update(networkBytes).digest("hex");
  if (expectedDigest && networkDigest !== expectedDigest) {
    throw new Error("Node loaded different GwaiNetPolicy bytes than the compiled SWF export");
  }
  const coreSource = fs.readFileSync(corePath, "utf8");
  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  const weights = readWeights(weightsPath, metadata.parameter_count);
  const easyRandom = seededRandom(samplerSeed ?? 1);
  let easyRandomDraws = 0;
  const policy = loadNetworkPolicy(
    networkSource,
    coreSource,
    metadata,
    weights,
    () => {
      easyRandomDraws++;
      return easyRandom();
    },
    easyTemperature,
    probeTiming,
  );
  const evaluate = policy.evaluate;
  const allRecords = fs.readFileSync(inputPath, "utf8").split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  const fixtureHeader = allRecords.find((record) => record.fixture_schema === 1) || null;
  if (fixtureHeader) {
    if (fixtureHeader.checkpoint_sha256 !== metadata.checkpoint_sha256) {
      throw new Error("fixtures were generated from a different checkpoint");
    }
    if (fixtureHeader.catalog_sha256 !== metadata.catalog_sha256) {
      throw new Error("fixtures were generated from a different card catalog");
    }
  }
  const fixtureLines = allRecords.filter((record) => !record.fixture_schema);
  const lines = positionLimit == null ? fixtureLines : fixtureLines.slice(0, positionLimit);
  let agreement = 0;
  let actionMismatches = 0;
  let decisionAgreement = 0;
  let decisionFallbacks = 0;
  let easyDecisionFallbacks = 0;
  let easyDecisionInvalids = 0;
  let easyDecisionTotal = 0;
  let observationMismatches = 0;
  let actionEncodingMismatches = 0;
  let logitsCompared = 0;
  let maxAbsError = 0;
  let totalAbsError = 0;
  let valueTotal = 0;
  let valueMaxAbsError = 0;
  let valueInvalid = 0;
  let insightTotal = 0;
  let insightAgreement = 0;
  const errors = [];
  const durations = [];
  const actionIds = [];
  const fallbackReasons = [];
  for (let index = 0; index < lines.length; index++) {
    const record = lines[index];
    const started = process.hrtime.bigint();
    const actual = evaluate(
      record.observation,
      record.legal_actions,
      metadata.card_ids,
      metadata.card_effects,
      weights,
      metadata.offsets,
      metadata.width,
      metadata.blocks,
    );
    durations.push(Number(process.hrtime.bigint() - started) / 1e6);
    const expected = record.expected;
    const view = {
      policyMode: "network",
      observation: record.observation,
      playerID: 0,
      opponentID: 1,
      scores: [0, 0],
      opponentPassed: false,
    };
    const decided = policy.decide(view, record.legal_actions, 2);
    const meterChance = policy.lastWinChance();
    if (actionSequenceHash) actionIds.push(policy.actionId(decided));
    if (view.policyFallback === true) {
      decisionFallbacks++;
      fallbackReasons.push(String(view.policyFallbackReason));
    }
    if (String(policy.actionId(decided)) === String(expected.action_id)) decisionAgreement++;
    if (easyTemperature > 0) {
      const easyView = { ...view, policyFallback: false };
      const easyAction = policy.decide(easyView, record.legal_actions, 1);
      if (easyView.policyFallback === true) easyDecisionFallbacks++;
      if (!record.legal_actions.some((action) => String(policy.actionId(action)) === String(policy.actionId(easyAction)))) {
        easyDecisionInvalids++;
      }
      easyDecisionTotal++;
    }
    if (actual.chosen_action_id === expected.action_id) agreement++;
    else {
      actionMismatches++;
      errors.push({ index, cause: "chosen action differs", expected: expected.action_id, actual: actual.chosen_action_id });
    }
    if (float32Hash(actual.observation) !== expected.observation_sha256) {
      observationMismatches++;
      errors.push({ index, cause: "schema v1 observation encoding differs" });
    }
    for (const field of ["action_features", "action_group_ids", "group_features"]) {
      if (JSON.stringify(actual[field]) !== JSON.stringify(expected[field])) {
        actionEncodingMismatches++;
        errors.push({ index, cause: `${field} differs` });
      }
    }
    if (expected.outcome_probabilities !== undefined) {
      valueTotal++;
      const insight = policy.insight(record.observation, record.legal_actions, 2);
      insightTotal++;
      if (String(insight.chosen_action_id) === String(expected.action_id)) insightAgreement++;
      const readings = [
        [actual.value, expected.value],
        [insight.value, expected.value],
        [meterChance, expected.outcome_probabilities[2]],
        // The coach's number: winChanceOf(insight) reads outcome_probabilities[2].
        [Array.isArray(insight.outcome_probabilities) ? insight.outcome_probabilities[2] : Number.NaN,
          expected.outcome_probabilities[2]],
      ];
      const probabilities = actual.outcome_probabilities;
      if (!Array.isArray(probabilities) || probabilities.length !== 3) valueInvalid++;
      else for (let item = 0; item < 3; item++) readings.push([probabilities[item], expected.outcome_probabilities[item]]);
      for (const [value, reference] of readings) {
        if (!Number.isFinite(Number(value))) {
          valueInvalid++;
          continue;
        }
        valueMaxAbsError = Math.max(valueMaxAbsError, Math.abs(Number(value) - Number(reference)));
      }
    }
    for (const field of ["group_logits", "local_logits"]) {
      const expectedValues = expected[field];
      const actualValues = actual[field];
      if (expectedValues.length !== actualValues.length) {
        errors.push({ index, cause: `${field} length differs`, expected: expectedValues.length, actual: actualValues.length });
        continue;
      }
      for (let item = 0; item < expectedValues.length; item++) {
        const error = Math.abs(Number(expectedValues[item]) - Number(actualValues[item]));
        maxAbsError = Math.max(maxAbsError, error);
        totalAbsError += error;
        logitsCompared++;
      }
    }
  }
  const sortedDurations = durations.slice().sort((left, right) => left - right);
  const median = sortedDurations[Math.floor(sortedDurations.length / 2)] || 0;
  const p95 = sortedDurations[Math.min(sortedDurations.length - 1, Math.floor(sortedDurations.length * 0.95))] || 0;
  const mean = durations.length ? durations.reduce((sum, value) => sum + value, 0) / durations.length : 0;
  const recordedEasyRandomDraws = easyRandomDraws;
  policy.resetRandom(samplerSeed ?? 1);
  const samplerDrawsResult = [];
  for (let draw = 0; draw < samplerDraws; draw++) {
    samplerDrawsResult.push(policy.sampleLogit([1.75, -0.25, 0.5], [0, 2], 0.7));
  }
  const result = {
    mode: "as3-policy-network-source",
    networkSourceSha256: networkDigest,
    fixtureHeader,
    agreement,
    total: lines.length,
    actionMismatches,
    decisionAgreement,
    decisionTotal: lines.length,
    decisionFallbacks,
    easyDecisionTotal,
    easyDecisionFallbacks,
    easyDecisionInvalids,
    easyRandomDraws: recordedEasyRandomDraws,
    weightLoadCalls: policy.weightLoadCalls(),
    valueTotal,
    valueInvalid,
    valueMaxAbsError,
    insightTotal,
    insightAgreement,
    valuePass: valueTotal === lines.length && valueInvalid === 0 && valueMaxAbsError <= tolerance &&
      insightAgreement === insightTotal,
    observationMismatches,
    actionEncodingMismatches,
    logitsCompared,
    logitsMaxAbsError: maxAbsError,
    logitsMeanAbsError: logitsCompared ? totalAbsError / logitsCompared : 0,
    tolerance,
    logitsPass: maxAbsError <= tolerance,
    encoderPass: observationMismatches === 0 && actionEncodingMismatches === 0,
    nodeMs: { mean, median, p95, max: Math.max(0, ...durations) },
    samplerSeed: samplerDraws ? samplerSeed : null,
    samplerDraws: samplerDrawsResult,
    mismatches: errors.slice(0, 20),
  };
  if (actionSequenceHash) {
    result.actionSequenceSha256 = crypto.createHash("sha256").update(actionIds.join("\n")).digest("hex");
  }
  if (probeTiming) {
    result.probeTiming = policy.timing();
    result.probeTimingErrors = fallbackReasons.slice(0, 20);
  }
  return result;
}

function main() {
  const options = {
    inputPath: path.join(__dirname, "fixtures.jsonl"),
    corePath: path.resolve(__dirname, "../as3/GwaiPolicyCore.as"),
    networkPath: path.resolve(__dirname, "../as3/GwaiNetPolicy.as"),
    metadataPath: path.resolve(__dirname, "../../../.runtime/policy-as3/metadata.json"),
    weightsPath: path.resolve(__dirname, "../../../.runtime/policy-as3/policy.f32.bin"),
    limit: null,
    expectedDigest: null,
    expectedNetworkDigest: null,
    networkEnabled: false,
    timingOnly: false,
    probeTiming: false,
    actionSequenceHash: false,
    positions: null,
    tolerance: 1e-4,
    samplerSeed: 1,
    samplerDraws: 0,
    easyTemperature: 0,
  };
  for (let index = 2; index < process.argv.length; index++) {
    const argument = process.argv[index];
    if (argument === "--record") options.inputPath = path.resolve(process.argv[++index]);
    else if (argument === "--core") options.corePath = process.argv[++index] === "-" ? "-" : path.resolve(process.argv[index]);
    else if (argument === "--network") {
      options.networkEnabled = true;
      options.networkPath = process.argv[++index] === "-" ? "-" : path.resolve(process.argv[index]);
    }
    else if (argument === "--metadata") options.metadataPath = path.resolve(process.argv[++index]);
    else if (argument === "--weights") options.weightsPath = path.resolve(process.argv[++index]);
    else if (argument === "--tolerance") options.tolerance = Number(process.argv[++index]);
    else if (argument === "--limit") options.limit = Number.parseInt(process.argv[++index], 10);
    else if (argument === "--expected-core-sha256") options.expectedDigest = process.argv[++index];
    else if (argument === "--expected-network-sha256") options.expectedNetworkDigest = process.argv[++index];
    else if (argument === "--positions") options.positions = Number.parseInt(process.argv[++index], 10);
    else if (argument === "--sampler-seed") options.samplerSeed = Number.parseInt(process.argv[++index], 10);
    else if (argument === "--sampler-draws") options.samplerDraws = Number.parseInt(process.argv[++index], 10);
    else if (argument === "--easy-temperature") options.easyTemperature = Number(process.argv[++index]);
    else if (argument === "--timing-only") options.timingOnly = true;
    else if (argument === "--probe-timing") options.probeTiming = true;
    else if (argument === "--action-sequence-hash") options.actionSequenceHash = true;
    else options.inputPath = path.resolve(argument);
  }
  if (options.limit != null && (!Number.isInteger(options.limit) || options.limit < 1)) throw new Error("--limit must be a positive integer");
  if (options.positions != null && (!Number.isInteger(options.positions) || options.positions < 1)) throw new Error("--positions must be a positive integer");
  if (!Number.isInteger(options.samplerSeed) || !Number.isInteger(options.samplerDraws) || options.samplerDraws < 0) {
    throw new Error("sampler seed must be an integer and sampler draws must be a non-negative integer");
  }
  if (!Number.isFinite(options.easyTemperature) || options.easyTemperature < 0) {
    throw new Error("Easy temperature must be finite and non-negative");
  }
  if (options.networkEnabled) {
    if (!fs.existsSync(options.metadataPath) || !fs.existsSync(options.weightsPath)) {
      throw new Error("network replay needs the exported metadata and float32 weights");
    }
    const summary = replayNetwork(
      options.inputPath,
      options.networkPath,
      options.corePath,
      options.metadataPath,
      options.weightsPath,
      options.tolerance,
      options.expectedNetworkDigest,
      options.positions,
      options.samplerSeed,
      options.samplerDraws,
      options.easyTemperature,
      options.probeTiming,
      options.actionSequenceHash,
    );
    process.stdout.write(`${JSON.stringify(summary)}\n`);
    if (!options.timingOnly && (
      summary.agreement !== summary.total ||
      summary.decisionAgreement !== summary.decisionTotal ||
      summary.decisionFallbacks !== 0 ||
      summary.easyDecisionFallbacks !== 0 ||
      summary.easyDecisionInvalids !== 0 ||
      summary.weightLoadCalls !== summary.decisionTotal + summary.easyDecisionTotal +
        summary.insightTotal ||
      !summary.valuePass ||
      !summary.logitsPass ||
      !summary.encoderPass
    )) process.exitCode = 1;
  } else {
  const policy = loadPolicyCore(options.corePath);
  if (options.expectedDigest && policy.digest !== options.expectedDigest) throw new Error("Node loaded different policy-core bytes than the exported SWF source");
  const result = replay(options.inputPath, policy.decide, options.limit);
  if (options.expectedDigest) result.coreSha256 = policy.digest;
  process.stdout.write(`${JSON.stringify(result)}\n`);
  }
}

if (require.main === module) main();

module.exports = { classBody, loadPolicyCore, loadNetworkPolicy };
