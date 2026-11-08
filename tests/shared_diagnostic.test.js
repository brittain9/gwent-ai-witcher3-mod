"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function diagnostic() {
  let source = fs.readFileSync(
    path.join(__dirname, "../mod/ai/as3/GwaiSharedDiagnostic.as"),
    "utf8",
  );
  source = source
    .slice(source.indexOf("{") + 1, source.lastIndexOf("}"))
    .replace(/(?:public|private) static var/g, "var")
    .replace(/(?:public|private) static function/g, "function")
    .replace(/\)\s*:\s*(?:void|String|int|Object|Boolean)\s*\{/g, ") {")
    .replace(
      /:\s*(?:Object|Array|Number|String|Boolean|int|uint)(?=[,\s)=;])/g,
      "",
    )
    .replace(/catch\((\w+):Error\)/g, "catch($1)")
    .replace(/\bint\s*\(/g, "Math.trunc(");
  const events = [];
  const math = Object.create(Math);
  math.random = () => {
    throw new Error("diagnostic must not consume action/native RNG");
  };
  function GameEvent(type, name, args) {
    this.name = name;
    this.args = args;
  }
  GameEvent.CALL = "call";
  const context = {
    JSON: undefined,
    Math: math,
    GwaiPlayTrace: { encodeDiagnostic: JSON.stringify },
    GwaiSharedConfig: {
      data: () => ({
        CHECKPOINT_SHA256: "a".repeat(64),
        EXPORT_SHA256: "b".repeat(64),
        OBSERVATION_PROFILE_ID: "faction-unknown-v1",
      }),
    },
    GwintGameMenu: {
      mSingleton: { dispatchEvent: (event) => events.push(event) },
    },
    red: { core: { events: { GameEvent } } },
  };
  vm.runInNewContext(
    `(function(){${source};this.api={begin,record,finish,rejectedRecords,lastRecord};}).call(this)`,
    context,
  );
  return { api: context.api, events };
}
function view() {
  return {
    seat: 1,
    decision_id: 3,
    pending_choice: null,
    terminal: false,
    hand: [{ template_id: 42 }],
    players: [
      { seat: 0, hand_count: 2 },
      { seat: 1, hand_count: 1 },
    ],
  };
}

test("diagnostic uses existing native sink without global JSON or another inference", () => {
  const runtime = diagnostic();
  runtime.api.begin();
  runtime.api.record(
    view(),
    [{ id: "pass", kind: "pass" }],
    { id: "pass", kind: "pass" },
    0.75,
  );
  runtime.api.finish("final");
  assert.equal(runtime.events[0].name, "OnW3GwentPolicyDiagnosticStart");
  const records = runtime.events
    .filter((event) => event.name === "OnW3GwentPolicyDiagnosticRecord")
    .map((event) => JSON.parse(event.args[0]));
  const input = records.find((record) => record.event === "policy_input");
  assert.ok(input, "real input reaches separate native diagnostic sink");
  assert.equal(input.observation.hand[0].template_id, 42);
  assert.equal(input.native_decision_id, 3);
  assert.equal(
    input.primary_turn,
    undefined,
    "ordinary policy record contract is preserved",
  );
  assert.deepEqual(
    records.map((record) => record.seq),
    records.map((_, index) => index),
  );
  assert.equal(runtime.events.at(-1).name, "OnW3GwentPolicyDiagnosticEnd");
  assert.equal(runtime.api.rejectedRecords(), 0);
});
test("diagnostic refuses expanded hidden fields before recording a policy input", () => {
  const runtime = diagnostic();
  runtime.api.begin();
  const observation = view();
  observation.opponent_hand = [{ template_id: 999 }];
  runtime.api.record(
    observation,
    [{ id: "pass", kind: "pass" }],
    { id: "pass", kind: "pass" },
    0.5,
  );
  const inputs = runtime.events
    .filter((event) => event.name === "OnW3GwentPolicyDiagnosticRecord")
    .map((event) => JSON.parse(event.args[0]))
    .filter((record) => record.event === "policy_input");
  assert.equal(inputs.length, 0);
  assert.ok(
    runtime.events.some(
      (event) => event.name === "OnW3GwentPolicyDiagnosticError",
    ),
  );
});
test("diagnostic bounds encoded records and reports refusal through the native sink", () => {
  const runtime = diagnostic();
  runtime.api.begin();
  const observation = view();
  observation.hand = [
    { template_id: 42, authored_fixture: "x".repeat(300000) },
  ];
  runtime.api.record(
    observation,
    [{ id: "pass", kind: "pass" }],
    { id: "pass", kind: "pass" },
    0.5,
  );
  assert.ok(runtime.api.rejectedRecords() > 0);
  assert.ok(
    runtime.events.some(
      (event) => event.name === "OnW3GwentPolicyDiagnosticError",
    ),
  );
  assert.equal(runtime.api.lastRecord(), "");
});
