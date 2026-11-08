"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PLAY = {
  id: "play:10:melee",
  kind: "play",
  instance_id: 10,
  row: "melee",
};
const PASS = { id: "pass", kind: "pass" };

function actorView() {
  return {
    seat: 1,
    decision_id: 7,
    pending_choice: null,
    terminal: false,
    hand: [{ instance_id: 10, template_id: 9001 }],
    players: [
      { seat: 0, hand_count: 4 },
      { seat: 1, hand_count: 2 },
    ],
  };
}

function loadController({
  dataKind = "gameplay",
  enabled = true,
  diagnostic = false,
  inferenceError = false,
  jsonAvailable = true,
  forgedAction = false,
} = {}) {
  let source = fs.readFileSync(
    path.join(ROOT, "mod/ai/as3/GwaiSharedController.as"),
    "utf8",
  );
  const marker = "/*__SHARED_DIAGNOSTIC_RECORD__*/";
  assert.equal(source.split(marker).length, 2, "one explicit diagnostic seam");
  source = source.replace(
    marker,
    diagnostic
      ? "GwaiSharedDiagnostic.record(this._observation,this._legal,this.output,this.value,result,this._difficulty);"
      : "",
  );
  source = source
    .replace(
      "internal class GwaiSharedController",
      "class GwaiSharedController",
    )
    .replace(/public function GwaiSharedController\s*\(/g, "constructor(")
    .replace(/(?:public|private) static function\s+(\w+)\s*\(/g, "static $1(")
    .replace(/(?:public|private) function\s+(\w+)\s*\(/g, "$1(")
    .replace(/(?:public|private) var\s+(\w+)\s*:\s*\w+/g, "$1")
    .replace(
      /\)\s*:\s*(?:Object|Array|Number|String|Boolean|int|void)\s*\{/g,
      ") {",
    )
    .replace(
      /:\s*(?:Object|Array|Number|String|Boolean|int|uint)(?=[,\s)=;])/g,
      "",
    )
    .replace(/flash\.utils\.getQualifiedClassName/g, "getQualifiedClassName")
    .replace(/\bas\s+(?:Object|Array)\b/g, "")
    .replace(/\bcatch\s*\((\w+)\s*:\s*Error\)/g, "catch ($1)")
    .replace(/\bint\s*\(/g, "Math.trunc(");

  const calls = [];
  const records = [];
  let randomCalls = 0;
  const model = {
    DATA_KIND: dataKind,
    CHECKPOINT_SHA256: "a".repeat(64),
    OBSERVATION_PROFILE_ID: "full-faction-v1",
    CARD_IDS: [9001, 9002],
    CARD_EFFECTS: [[], [4]],
    OFFSETS: {},
    WIDTH: 2,
    BLOCKS: 1,
  };
  const math = Object.create(Math);
  math.random = () => {
    randomCalls++;
    return 0.5;
  };
  const context = {
    JSON: jsonAvailable ? JSON : undefined,
    Math: math,
    GwaiSharedConfig: {
      enabled: () => enabled,
      mode: () => "policy-only",
      data: () => model,
      settingsFor: (difficulty) => {
        if (!Number.isInteger(difficulty) || difficulty < 1 || difficulty > 3) {
          throw new Error("difficulty outside 1..3");
        }
        return {
          temperature: 0,
          planning_worlds: 0,
          rollout_primary_turns: 0,
          deadline_ms: 0,
        };
      },
    },
    GwaiNetWeights: {
      load: (data) => {
        assert.equal(data, model);
        return [0.125];
      },
    },
    GwaiNetPolicy: {
      evaluate: (
        observation,
        legal,
        ids,
        effects,
        weights,
        offsets,
        width,
        blocks,
        temperature,
        profile,
      ) => {
        math.random();
        calls.push(
          JSON.parse(
            JSON.stringify({ observation, legal, temperature, profile }),
          ),
        );
        if (inferenceError)
          throw new Error("declared synthetic inference error");
        return {
          chosen_index: 0,
          chosen_action: forgedAction
            ? { ...legal[0], instance_id: 999 }
            : legal[0],
          value: 0.75,
          outcome_probabilities: [0.2, 0.1, 0.7],
        };
      },
    },
    GwaiSharedDiagnostic: {
      record: (...args) => records.push(JSON.stringify(args)),
    },
    getQualifiedClassName: (value) => {
      if (value === null) return "null";
      if (Array.isArray(value)) return "Array";
      if (typeof value === "number") return "Number";
      if (typeof value === "string") return "String";
      if (typeof value === "boolean") return "Boolean";
      return value.constructor?.name ?? "Object";
    },
  };
  vm.runInNewContext(
    `${source}\nthis.Controller = GwaiSharedController;`,
    context,
  );
  return {
    Controller: context.Controller,
    calls,
    records,
    draws: () => randomCalls,
  };
}

test("one shared checkpoint serves all three native difficulty settings", () => {
  const runtime = loadController();
  for (const difficulty of [1, 2, 3]) {
    const controller = new runtime.Controller(difficulty);
    controller.begin(actorView(), [PLAY, PASS]);
    assert.equal(controller.advance(), true);
    assert.equal(controller.status, "complete");
    assert.equal(JSON.stringify(controller.output), JSON.stringify(PLAY));
    assert.equal(controller.value, 0.75);
  }
  assert.equal(runtime.calls.length, 3);
  assert.equal(
    runtime.calls.every((call) => call.profile === "full-faction-v1"),
    true,
  );
});

test("disabled shared behavior does not touch input or invoke inference", () => {
  const runtime = loadController({ enabled: false });
  const controller = new runtime.Controller(3);
  controller.begin(null, null);
  assert.equal(controller.advance(), true);
  assert.equal(controller.status, "disabled");
  assert.equal(controller.output, null);
  assert.equal(runtime.calls.length, 0);
});

test("capture data is copied so later caller mutation cannot change the decision", () => {
  const runtime = loadController();
  const controller = new runtime.Controller(3);
  const view = actorView();
  const legal = [JSON.parse(JSON.stringify(PLAY)), PASS];
  controller.begin(view, legal);
  view.hand[0].template_id = 9999;
  legal[0].row = "siege";
  controller.advance();
  assert.equal(runtime.calls[0].observation.hand[0].template_id, 9001);
  assert.equal(runtime.calls[0].legal[0].row, "melee");
});

test("native pending and terminal observations cannot request another model action", () => {
  for (const changed of [
    { pending_choice: { kind: "nurse" } },
    { terminal: true },
  ]) {
    const runtime = loadController();
    const controller = new runtime.Controller(3);
    assert.throws(() =>
      controller.begin({ ...actorView(), ...changed }, [PLAY, PASS]),
    );
    assert.equal(runtime.calls.length, 0);
  }
});

test("live native objects are rejected instead of cloned by reference", () => {
  const runtime = loadController();
  const controller = new runtime.Controller(3);
  class CardManager {}
  const view = actorView();
  view.manager = new CardManager();
  assert.throws(
    () => controller.begin(view, [PLAY, PASS]),
    /data|native|object/i,
  );
  assert.equal(runtime.calls.length, 0);
});

test("cancellation prevents an outstanding primary inference", () => {
  const runtime = loadController();
  const controller = new runtime.Controller(3);
  controller.begin(actorView(), [PLAY, PASS]);
  controller.cancel();
  assert.equal(controller.advance(), true);
  assert.equal(controller.status, "cancelled");
  assert.equal(controller.output, null);
  assert.equal(runtime.calls.length, 0);
});

test("diagnostic recording changes neither decision nor action RNG consumption", () => {
  const release = loadController();
  const diagnostic = loadController({ diagnostic: true });
  for (const runtime of [release, diagnostic]) {
    const controller = new runtime.Controller(3);
    controller.begin(actorView(), [PLAY, PASS]);
    controller.advance();
  }
  assert.deepEqual(release.calls, diagnostic.calls);
  assert.equal(release.draws(), diagnostic.draws());
  assert.equal(release.records.length, 0);
  assert.equal(diagnostic.records.length, 1);
});

test("an inference error is explicit and cannot rerun the same primary decision", () => {
  const runtime = loadController({ inferenceError: true });
  const controller = new runtime.Controller(3);
  controller.begin(actorView(), [PLAY, PASS]);
  assert.throws(() => controller.advance(), /synthetic inference error/);
  assert.equal(controller.status, "error");
  assert.equal(controller.output, null);
  assert.equal(controller.done, true);
  assert.equal(controller.advance(), true);
  assert.equal(runtime.calls.length, 1);
});

test("native runtime without JSON completes one canonical primary decision", () => {
  const runtime = loadController({ jsonAvailable: false });
  const controller = new runtime.Controller(3);
  controller.begin(actorView(), [PLAY, PASS]);
  assert.equal(controller.advance(), true);
  assert.equal(controller.status, "complete");
  assert.deepEqual({ ...controller.output }, PLAY);
  assert.equal(runtime.calls.length, 1);
});

test("native runtime without JSON still rejects a forged canonical action", () => {
  const runtime = loadController({ jsonAvailable: false, forgedAction: true });
  const controller = new runtime.Controller(3);
  controller.begin(actorView(), [PLAY, PASS]);
  assert.throws(() => controller.advance(), /noncanonical primary action/);
  assert.equal(controller.status, "error");
  assert.equal(controller.output, null);
  assert.equal(runtime.calls.length, 1);
});

test("inference-only exports run without invented training provenance", () => {
  const runtime = loadController({ dataKind: "inference-only" });
  const controller = new runtime.Controller(3);
  controller.begin(actorView(), [PLAY, PASS]);
  controller.advance();
  assert.equal(controller.sourceKind, "network");
  assert.equal(runtime.calls.length, 1);
});

test("legacy insight forwards the selected export observation profile", () => {
  const source = fs.readFileSync(
    path.join(ROOT, "mod/ai/as3/GwaiNetPolicy.as"),
    "utf8",
  );
  const body = /function insight\([^]*?\) : Object\s*\{([^]*?)\n   \}/
    .exec(source)[1]
    .replace(/var (\w+):(?:Object|Vector\.<Number>)/g, "var $1");
  let forwarded;
  const context = {
    GwaiNetTierConfig: {
      dataFor: () => ({ OBSERVATION_PROFILE_ID: "faction-unknown-v1" }),
    },
    GwaiNetWeights: { load: () => [] },
    evaluate: (...args) => {
      forwarded = args;
      return "reading";
    },
    String,
  };
  vm.runInNewContext(
    "function insight(observation,legalActions,difficulty){" +
      body +
      "};result=insight({},[],3);",
    context,
  );
  assert.equal(context.result, "reading");
  assert.equal(forwarded[9], "faction-unknown-v1");
});
