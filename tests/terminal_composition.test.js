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
  enabled = true,
  terminalPass = false,
  diagnostic = false,
  inferenceError = false,
  jsonAvailable = true,
  forgedAction = false,
  compiledSourcePath = null,
} = {}) {
  const compiled = compiledSourcePath || process.env.GWAI_SHARED_CONTROLLER_AS3;
  let source = fs.readFileSync(
    compiled || path.join(ROOT, "mod/ai/as3/GwaiSharedController.as"),
    "utf8",
  );
  if (compiled) {
    const { classBody } = require("../mod/ai/node/shared_numeric");
    source =
      "internal class GwaiSharedController {\n" +
      classBody(source, "GwaiSharedController") +
      "\n}";
    source = source.replace(/\bsuper\(\);/g, "");
  }
  const marker = "/*__SHARED_DIAGNOSTIC_RECORD__*/";
  assert.equal(
    source.split(marker).length,
    compiled ? 1 : 2,
    "one authored seam; compiled body has resolved diagnostics",
  );
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
    .replace(
      /(?:public|private) var\s+(\w+)\s*:\s*(?:Vector\.<Number>|\*|\w+)/g,
      "$1",
    )
    .replace(
      /\)\s*:\s*(?:Vector\.<Number>|Object|Array|Number|String|Boolean|int|void|\*)\s*\{/g,
      ") {",
    )
    .replace(
      /:\s*(?:Vector\.<Number>|Object|Array|Number|String|Boolean|int|uint|\*)(?=[,\s)=;])/g,
      "",
    )
    .replace(/flash\.utils\.getQualifiedClassName/g, "getQualifiedClassName")
    .replace(/\bas\s+(?:Object|Array)\b/g, "")
    .replace(/\bcatch\s*\((\w+)\s*:\s*Error\)/g, "catch ($1)")
    .replace(/\bint\s*\(/g, "Math.trunc(");

  const calls = [];
  const records = [];
  let weightLoads = 0;
  let randomCalls = 0;
  const model = {
    DATA_KIND: "gameplay",
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
    getTimer: () => 0,
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
          terminal_pass_v1: terminalPass,
          planning_worlds: 0,
          rollout_primary_turns: 0,
          deadline_ms: 0,
        };
      },
    },
    GwaiNetWeights: {
      load: (data) => {
        weightLoads++;
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
      timingMark: () => {},
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
    model,
    loads: () => weightLoads,
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

test("terminal pass selects a public solved match before inference", () => {
  const runtime = loadController({ terminalPass: true });
  const controller = new runtime.Controller(3);
  const observation = actorView();
  observation.scores = [4, 7];
  observation.lives = [1, 1];
  observation.players[0].passed = true;
  observation.players[1].passed = false;
  controller.begin(observation, [PLAY, PASS]);
  controller.advance();
  assert.equal(
    controller.output.id,
    "pass",
    "a guaranteed match win selects canonical pass before inference",
  );
  assert.equal(
    runtime.calls.length,
    0,
    "solved terminal pass does not invoke the model",
  );
  assert.equal(
    runtime.draws(),
    0,
    "solved terminal pass does not consume policy RNG",
  );
});

function solvedView(seat = 1) {
  const view = actorView();
  view.seat = seat;
  view.scores = seat === 1 ? [4, 7] : [7, 4];
  view.lives = [1, 1];
  view.players = [
    { seat: 0, passed: seat !== 0, hand_count: 5 },
    { seat: 1, passed: seat !== 1, hand_count: 8 },
  ];
  return view;
}

function decideSolved(runtime, view, legal = [PLAY, PASS], difficulty = 3) {
  const controller = new runtime.Controller(difficulty);
  controller.begin(view, legal);
  controller.advance();
  return controller;
}

test("terminal pass is default off and strict opt-in independently of difficulty", () => {
  for (const flag of [false, undefined, "true", 1]) {
    for (const difficulty of [1, 2, 3]) {
      const runtime = loadController({ terminalPass: flag });
      const result = decideSolved(
        runtime,
        solvedView(),
        [PLAY, PASS],
        difficulty,
      );
      assert.equal(result.output.id, PLAY.id);
      assert.equal(runtime.calls.length, 1);
      assert.equal(runtime.draws(), 1);
      assert.equal(result.sourceKind, "network");
      assert.equal(result.modelCall, true);
    }
  }
});

test("terminal pass preserves canonical action and honest nonneural attribution", () => {
  for (const seat of [0, 1]) {
    for (const difficulty of [1, 2, 3]) {
      const runtime = loadController({ terminalPass: true });
      const result = decideSolved(
        runtime,
        solvedView(seat),
        [PLAY, PASS],
        difficulty,
      );
      assert.deepEqual({ ...result.output }, PASS);
      assert.equal(result.sourceKind, "terminal-pass-guard");
      assert.equal(result.modelCall, false);
      assert.equal(Number.isNaN(result.value), true);
      assert.equal(result.status, "complete");
      assert.equal(runtime.calls.length, 0);
      assert.equal(runtime.loads(), 0);
      assert.equal(runtime.draws(), 0);
    }
  }
});

test("terminal pass ignores irrelevant public payload and opponent remaining options", () => {
  for (const seat of [0, 1]) {
    for (const handCount of [0, 15]) {
      const runtime = loadController({ terminalPass: true });
      const view = solvedView(seat);
      view.players[1 - seat].hand_count = handCount;
      view.players[1 - seat].leader_used = false;
      view.players[1 - seat].faction = 2;
      view.players[seat].graveyard = [{ instance_id: 55, template_id: 9002 }];
      view.players[1 - seat].graveyard = [
        { instance_id: 65, template_id: 9001 },
      ];
      view.hand.reverse();
      view.lives[seat] = 2;
      view.players.reverse();
      view.to_move = seat;
      const result = decideSolved(runtime, view);
      assert.equal(result.output.id, "pass");
      assert.equal(runtime.calls.length, 0);
    }
  }
});

test("terminal pass refuses round-only wins ties and malformed sufficient-condition data", () => {
  const mutations = [
    (v) => {
      v.lives[0] = 2;
    },
    (v) => {
      v.scores = [7, 7];
    },
    (v) => {
      v.scores = [8, 7];
    },
    (v) => {
      v.players[0].passed = false;
    },
    (v) => {
      v.players[1].passed = true;
    },
    (v) => {
      v.lives = [1, 0];
    },
    (v) => {
      delete v.lives;
    },
    (v) => {
      v.lives = ["1", 1];
    },
    (v) => {
      v.scores = [false, 7];
    },
    (v) => {
      v.scores = [4, "7"];
    },
    (v) => {
      delete v.scores;
    },
    (v) => {
      delete v.players[0].passed;
    },
    (v) => {
      v.players[0].passed = 1;
    },
    (v) => {
      v.terminal = null;
    },
  ];
  for (const mutate of mutations) {
    const runtime = loadController({ terminalPass: true });
    const view = solvedView();
    mutate(view);
    const result = decideSolved(runtime, view);
    assert.equal(result.output.id, PLAY.id);
    assert.equal(runtime.calls.length, 1);
    assert.equal(result.sourceKind, "network");
  }
});

test("terminal pass requires original exact canonical pass and never synthesizes one", () => {
  for (const legal of [
    [PLAY],
    [PLAY, { id: "pass-other", kind: "pass" }],
    [PLAY, { id: "pass", kind: "play" }],
    [PLAY, { id: "pass", kind: "pass", row: "melee" }],
  ]) {
    const runtime = loadController({ terminalPass: true });
    const result = decideSolved(runtime, solvedView(), legal);
    assert.equal(result.output.id, PLAY.id);
    assert.equal(runtime.calls.length, 1);
  }
});

test("terminal pass cannot bypass pending terminal actor or hidden-expanded admission", () => {
  for (const fields of [
    { pending_choice: { kind: "nurse" } },
    { terminal: true },
    { terminal: { winner: 1 } },
    { to_move: 0 },
    { opponent_hand: [9002] },
  ]) {
    const runtime = loadController({ terminalPass: true });
    const controller = new runtime.Controller(3);
    assert.throws(() =>
      controller.begin({ ...solvedView(), ...fields }, [PLAY, PASS]),
    );
    assert.equal(runtime.calls.length, 0);
    assert.equal(controller.output, null);
  }
});

test("terminal pass retains explicit gameplay profile admission before selection", () => {
  for (const bad of ["scope", "profile"]) {
    const runtime = loadController({ terminalPass: true });
    if (bad === "scope") runtime.model.DATA_KIND = "numeric-fixture";
    else runtime.model.OBSERVATION_PROFILE_ID = "unknown-profile";
    const controller = new runtime.Controller(3);
    controller.begin(solvedView(), [PLAY, PASS]);
    assert.throws(() => controller.advance());
    assert.equal(runtime.calls.length, 0);
    assert.equal(controller.output, null);
  }
});

test("terminal pass copies admitted data and cancellation keeps it unselected", () => {
  const runtime = loadController({ terminalPass: true });
  const view = solvedView();
  const legal = [PLAY, { ...PASS }];
  const controller = new runtime.Controller(3);
  controller.begin(view, legal);
  view.scores = [100, 0];
  legal[1].kind = "play";
  controller.advance();
  assert.deepEqual({ ...controller.output }, PASS);
  const cancelled = new runtime.Controller(3);
  cancelled.begin(solvedView(), [PLAY, PASS]);
  cancelled.cancel();
  assert.equal(cancelled.advance(), true);
  assert.equal(cancelled.output, null);
  assert.equal(cancelled.modelCall, false);
  assert.equal(runtime.calls.length, 0);
});

test("terminal pass needs neither JSON runtime nor neural diagnostics", () => {
  const runtime = loadController({
    terminalPass: true,
    jsonAvailable: false,
    diagnostic: true,
    inferenceError: true,
  });
  const result = decideSolved(runtime, solvedView());
  assert.equal(result.output.id, "pass");
  assert.equal(result.modelCall, false);
  assert.equal(runtime.records.length, 0);
  assert.equal(runtime.loads(), 0);
});
