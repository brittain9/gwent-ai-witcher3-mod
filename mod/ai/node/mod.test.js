"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../../..");
const RUNNER_SOURCE = fs.readFileSync(path.join(ROOT, "mod/ai/as3/GwaiRunner.as"), "utf8");
const BUILD_MANIFEST = process.env.W3_MOD_MANIFEST
  ? JSON.parse(fs.readFileSync(process.env.W3_MOD_MANIFEST, "utf8"))
  : null;

function loadRunner(policy, network) {
  const source = RUNNER_SOURCE
    .replace("internal class GwaiRunner", "class GwaiRunner")
    .replace(/private static const (\w+):Array\s*=\s*(\[[^;]*\]);/g, "static $1 = $2;")
    .replace(/(?:private|public) var (\w+):[\w.<>]+(\s*=\s*[^;]+)?;/g,
      (_, name, initial = "") => `${name}${initial};`)
    .replace(/\b(MOVES_PER_FRAME|MAX_EVALUATIONS)\[/g, "GwaiRunner.$1[")
    .replace(/public function GwaiRunner\(([^)]*)\)\s*\{/, "constructor($1) {")
    .replace(/(?:public|private) function\s+(\w+)\(([^)]*)\)\s*:\s*(?:Object|Number|Boolean|int|uint|void)\s*\{/g, "$1($2) {")
    .replace(/\b([A-Za-z_$][\w$]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b/g, "$1");
  const moduleShim = { exports: {} };
  vm.runInNewContext(`${source}\nmodule.exports = GwaiRunner;`, {
    module: moduleShim,
    Math,
    Number,
    Error,
    int: Math.trunc,
    GwaiPolicyCore: policy,
    GwaiNetPolicy: network,
  });
  return moduleShim.exports;
}

test("a computed network move is available on the first advance without a clock", () => {
  const selected = { id: "play:17:melee", kind: "play" };
  const legal = [selected, { id: "pass", kind: "pass" }];
  const view = { policyMode: "network" };
  let evaluations = 0;
  const Runner = loadRunner({
    decide(actualView, actualLegal, tier) {
      assert.equal(actualView, view);
      assert.equal(actualLegal, legal);
      assert.equal(tier, 3);
      evaluations++;
      return selected;
    },
  }, { lastWinChance: () => 0.75 });
  const runner = new Runner(view, legal, 3);
  assert.equal(runner.done, false);
  assert.equal(runner.advance(), true);
  assert.equal(runner.done, true);
  assert.equal(runner.output, selected);
  assert.equal(runner.winChance, 0.75);
  assert.equal(runner.advance(), true);
  assert.equal(evaluations, 1);
});

test("the three real computation limits preserve their selected move without a delay", () => {
  for (const [tier, limit] of [[1, 1], [2, 4], [3, 16]]) {
    const legal = Array.from({ length: 20 }, (_, id) => ({ id, kind: "play" }));
    const scored = [];
    const Runner = loadRunner({
      scoreMove(view, move) { scored.push(move); },
      decide(view, candidates) {
        assert.equal(candidates.length, limit);
        return candidates[candidates.length - 1];
      },
    }, {});
    const runner = new Runner({}, legal, tier);
    assert.equal(runner.advance(), true);
    assert.deepEqual(scored, legal.slice(0, limit));
    assert.equal(runner.output, legal[limit - 1]);
  }
});

test("a cancelled runner cannot evaluate or release a stale move", () => {
  const Runner = loadRunner({ decide() { assert.fail("cancelled policy evaluated"); } }, {});
  const runner = new Runner({ policyMode: "network" }, [{ kind: "pass" }], 3);
  runner.cancel();
  assert.equal(runner.done, true);
  assert.equal(runner.output, null);
  assert.equal(runner.advance(), true);
  assert.equal(runner.output, null);
});

test("a policy fallback does not expose a stale network value", () => {
  const view = { policyMode: "network" };
  const selected = { id: "pass", kind: "pass" };
  const Runner = loadRunner({ decide() { view.policyFallback = true; return selected; } },
    { lastWinChance() { assert.fail("fallback must not expose a network reading"); } });
  const runner = new Runner(view, [selected], 2);
  assert.equal(runner.advance(), true);
  assert.equal(runner.output, selected);
  assert.equal(Number.isNaN(runner.winChance), true);
});

test("the runner rejects policy tiers outside Easy, Normal and Hard", () => {
  const Runner = loadRunner({}, {});
  for (const tier of [0, 4]) assert.throws(() => new Runner({}, [], tier), /outside 1..3/);
});

test("package manifest records the complete three-tier ladder", { skip: !BUILD_MANIFEST }, () => {
  const build = BUILD_MANIFEST;
  const ladder = build.difficulty_ladder;
  assert.equal(build.status, "offline_compiled_and_export_verified");
  assert.equal(build.format, "witcher3-gwent-mod-build-v4");
  assert.deepEqual(Object.keys(ladder.tiers).sort(), ["easy", "hard", "normal"]);
  assert.deepEqual(Object.keys(build.tier_policies).sort(), ["easy", "hard", "normal"]);
  assert.equal(ladder.vanilla_difficulty_fact, "gwent_difficulty");
  assert.deepEqual(ladder.preserves, ["NPC deck selection", "cards", "rules", "rewards", "collection progression"]);
  for (const [factValue, tier] of [[1, "easy"], [2, "normal"], [3, "hard"]]) {
    const entry = ladder.tiers[tier];
    assert.equal(entry.game_difficulty.fact_value, factValue);
    assert.equal(entry.selected_by, "the game's Gwent difficulty setting or explicit practice tier");
    assert.match(entry.checkpoint_sha256, /^[0-9a-f]{64}$/);
    assert.equal(entry.checkpoint_sha256, build.tier_policies[tier].checkpoint_sha256);
    assert.equal(entry.temperature, build.tier_policies[tier].temperature);
  }
});

require("./fun.test.js");
require("./weights.test.js");
require("./unknown.test.js");
