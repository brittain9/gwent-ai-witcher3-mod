"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function host(temperature = 1) {
  const source = fs.readFileSync(
    path.join(__dirname, "../shared_player/owned_actions.py"),
    "utf8",
  );
  const raw = /FLOW = r\"\"\"([\s\S]*?)\"\"\"/.exec(source);
  assert.ok(raw, "actual owned-action Flow source missing");
  const body = raw[1]
    .replace(
      /(?:public|private) var\s+(\w+)\s*:\s*(?:Object|Number|Array|uint)/g,
      "$1",
    )
    .replace(/(?:public|private) function\s+(\w+)\s*\(/g, "$1(")
    .replace(
      /:\s*(?:Object|Number|Array|uint|int|void|Boolean)(?=[,\s)=;{])/g,
      "",
    )
    .replace(/\buint\(/g, "u32(");
  let draws = 0;
  const math = Object.create(Math);
  math.random = () => {
    throw new Error("live game RNG used");
  };
  const policySource = fs.readFileSync(
    path.join(__dirname, "../mod/ai/as3/GwaiNetPolicy.as"),
    "utf8",
  );
  const start = policySource.indexOf(
    "public static function ownedActionUniforms(",
  );
  const end = policySource.indexOf(
    "private static function temperatureWeights",
    start,
  );
  assert.ok(start >= 0 && end > start, "actual numeric-seed helper required");
  const helper = policySource
    .slice(start, end)
    .replace(
      "public static function ownedActionUniforms",
      "function actualUniforms",
    )
    .replace(/:\s*(?:Object|Number|Array|uint|int|void)(?=[,\s)=;{])/g, "");
  const context = {
    Math: math,
    u32: (x) => Number(x) >>> 0,
    GwaiSharedConfig: {
      enabled: () => true,
      settingsFor: () => ({ temperature }),
    },
  };
  vm.runInNewContext(helper + ";this.numericUniforms=actualUniforms;", context);
  context.GwaiNetPolicy = {
    ownedActionUniforms(seed) {
      const result = context.numericUniforms(seed);
      draws += 2;
      return result;
    },
  };
  vm.runInNewContext("class Flow{" + body + "};this.Flow=Flow;", context);
  const flow = new context.Flow();
  flow.gwaiPrimaryCounterValid = () => true;
  flow.gwaiPrimaryObservationReady = (seat) => seat === 1;
  flow.gwaiPrimaryTicket = () => 7;
  return { flow, draws: () => draws, numericUniforms: context.numericUniforms };
}
test("Hard zero temperature consumes no action draws", () => {
  const h = host(0);
  h.flow.gwaiOwnedActionReset(123);
  assert.equal(h.flow.gwaiOwnedActionUniforms(3, 1, 7), null);
  assert.equal(h.draws(), 0);
});
test("same primary ticket reuses owned copied uniforms exactly once", () => {
  const h = host();
  h.flow.gwaiOwnedActionReset(123);
  const a = h.flow.gwaiOwnedActionUniforms(1, 1, 7);
  a[0] = 99;
  const b = h.flow.gwaiOwnedActionUniforms(1, 1, 7);
  assert.notEqual(b[0], 99);
  assert.equal(h.draws(), 2);
});
test("stale or opposing ticket refuses before consuming RNG", () => {
  const h = host();
  h.flow.gwaiOwnedActionReset(123);
  assert.throws(() => h.flow.gwaiOwnedActionUniforms(1, 0, 7));
  assert.throws(() => h.flow.gwaiOwnedActionUniforms(1, 1, 8));
  assert.equal(h.draws(), 0);
});
test("match reset reproduces the independently seeded stream without live RNG", () => {
  const h = host();
  h.flow.gwaiOwnedActionReset(123);
  const a = h.flow.gwaiOwnedActionUniforms(1, 1, 7);
  h.flow.gwaiOwnedActionReset(123);
  const b = h.flow.gwaiOwnedActionUniforms(1, 1, 7);
  assert.deepEqual(Array.from(a), Array.from(b));
  assert.equal(h.draws(), 4);
});

test("actual numeric helper advances the host state across multiple tickets", () => {
  for (const seed of [0, 1, 123, 4294967295]) {
    const h = host();
    h.flow.gwaiOwnedActionReset(seed);
    let expected = h.flow.gwaiOwnedActionState().state;
    for (let ticket = 1; ticket <= 4; ticket++) {
      h.flow.gwaiPrimaryTicket = () => ticket;
      const pair = Array.from(h.numericUniforms(expected));
      const actual = Array.from(h.flow.gwaiOwnedActionUniforms(1, 1, ticket));
      assert.deepEqual(actual, pair);
      expected = (pair[1] * 4294967296) >>> 0;
      assert.equal(h.flow.gwaiOwnedActionState().state, expected);
      assert.equal(h.flow.gwaiOwnedActionState().cursor, ticket * 2);
    }
  }
});
