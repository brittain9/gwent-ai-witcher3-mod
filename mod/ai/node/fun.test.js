"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../../..");
const SOURCE = fs.readFileSync(path.join(ROOT, "mod/ai/as3/GwaiFunLogic.as"), "utf8");
const BUILD_MANIFEST = process.env.W3_MOD_MANIFEST
  ? JSON.parse(fs.readFileSync(process.env.W3_MOD_MANIFEST, "utf8"))
  : null;

// AS3 int(): non-finite values become 0, others truncate to a 32-bit integer.
function as3Int(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) | 0 : 0;
}

function loadLogic() {
  const open = SOURCE.indexOf("{", SOURCE.indexOf("internal class GwaiFunLogic"));
  const body = SOURCE.slice(open + 1, SOURCE.lastIndexOf("}"));
  const javascript = body
    .replace(/(?:public|private) static const\s+(\w+)\s*:\s*\w+\s*=/g, "const $1 =")
    .replace(/public static function\s+(\w+)\(([^)]*)\)\s*:\s*\w+\s*\{/g, "function $1($2) {")
    // Type annotations only: a type name followed by "(" is a conversion call, kept as is.
    .replace(/\b([A-Za-z_$][\w$]*)\s*:\s*(?:Object|Array|Number|String|Boolean|int|uint)\b(?!\s*\()/g, "$1");
  const moduleShim = { exports: {} };
  vm.runInNewContext(
    `${javascript}\nmodule.exports = { featureFlags, tierFor, percent, moveLabel, coachLine, meterSuffix, blunderLine,` +
      " isBlunder, samePlacement, costliest, agreement, postGameLines, outcomeName, tierBucket, bucketLabel, safeKey," +
      " statsDeckKey, settingsText, recordText, countsAfter, statsLine };",
    { module: moduleShim, Math, Number, String, isNaN, int: as3Int },
  );
  return moduleShim.exports;
}

const logic = loadLogic();

// Arrays built inside the VM context have that context's prototype; compare plain copies.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function move(round, before, after, agreed, actualLabel = "Ves", pickLabel = "pass", atResult = false) {
  return { round, before, after, agreed, actualLabel, pickLabel, atResult };
}

test("the costliest move is the largest drop among moves that left the network's pick", () => {
  const records = [
    move(1, 0.6, 0.55, false),
    move(1, 0.55, 0.2, true),
    move(2, 0.7, 0.4, false, "Geralt"),
    move(2, 0.5, Number.NaN, false),
  ];
  assert.equal(logic.costliest(records, 0.05), 2);
  assert.equal(logic.costliest([move(1, 0.6, 0.57, false)], 0.05), -1);
  assert.deepEqual(plain(logic.agreement(records)), [1, 4]);
});

test("the post-game card names the move, both win chances and the network's pick", () => {
  const lines = plain(logic.postGameLines([move(1, 0.61, 0.6, true), move(2, 0.62, 0.38, false, "Ves", "pass")], 0.05));
  assert.deepEqual(lines, [
    "Costliest move: round 2, Ves (your win chance 62% before it, 38% at your next turn).",
    "The network's pick was pass. You matched the network's pick on 1 of 2 moves.",
  ]);
  // The last move of the match is read against the result.
  assert.deepEqual(plain(logic.postGameLines([move(3, 0.45, 0, false, "pass", "Geralt to melee", true)], 0.05))[0],
    "Costliest move: round 3, pass (your win chance 45% before it, 0% at the result).");
  assert.deepEqual(plain(logic.postGameLines([move(1, 0.5, 0.49, false)], 0.05)), [
    "Post-game: no costly move found. You matched the network's pick on 0 of 1 moves.",
  ]);
  assert.deepEqual(plain(logic.postGameLines([], 0.05)), [
    "Post-game: no costly move found. You matched the network's pick on 0 of 0 moves.",
  ]);
});

test("labels and banners read plainly and tolerate a missing value", () => {
  assert.equal(logic.percent(0.644), "64%");
  assert.equal(logic.percent(1.2), "100%");
  assert.equal(logic.percent(Number.NaN), "?");
  assert.equal(logic.moveLabel("pass", "", null, null, false), "pass");
  assert.equal(logic.moveLabel("leader", "", null, null, false), "your leader ability");
  assert.equal(logic.moveLabel("play", "Ves", "melee", null, false), "Ves to melee");
  assert.equal(logic.moveLabel("play", "Thaler", "siege", null, true), "Thaler to their siege");
  assert.equal(logic.moveLabel("play", "Decoy", null, "Ves", false), "Decoy on Ves");
  assert.equal(logic.moveLabel("play", "", null, null, false), "a card");
  assert.equal(logic.coachLine("Ves to melee", 0.41), "Coach: Ves to melee (your win chance 41%)");
  assert.equal(logic.coachLine("pass", Number.NaN), "Coach: pass");
  assert.equal(logic.meterSuffix(0.64), "; AI win chance 64%");
  assert.equal(logic.meterSuffix(Number.NaN), "");
  assert.equal(
    logic.blunderLine(0.234, "pass", "Ves to melee"),
    "Coach: since your last move (pass), your win chance fell about 23 points. The network's pick was Ves to melee.",
  );
});

test("lab mode gates the analysis tools; coach and story stand alone", () => {
  const all = { coach: true, story: true, meter: true, postGame: true, blunder: true, stats: true };
  const off = logic.featureFlags({ ...all, lab: false });
  assert.deepEqual([off.coach, off.story, off.meter, off.postGame, off.blunder, off.stats],
    [true, true, false, false, false, false]);
  const on = logic.featureFlags({ ...all, lab: true });
  assert.deepEqual([on.meter, on.postGame, on.blunder, on.stats], [true, true, true, true]);
  const none = logic.featureFlags({ lab: true });
  assert.deepEqual([none.coach, none.story, none.meter, none.postGame, none.blunder, none.stats],
    [false, false, false, false, false, false]);
  const counts = logic.featureFlags({ lab: true, stats: true, stats_hard_won: 3, stats_deck_lost: -2 }).counts;
  assert.equal(counts.hard_won, 3);
  assert.equal(counts.deck_lost, 0);
  assert.deepEqual(Object.keys(counts).sort(),
    ["vanilla", "easy", "normal", "hard", "deck"].flatMap((prefix) =>
      ["won", "lost", "drawn"].map((outcome) => `${prefix}_${outcome}`)).sort());
  assert.equal(logic.featureFlags({}).deck, "");
});

test("the three tiers follow the game's mapping, raised but never lowered by the story", () => {
  // Story off (0): the vanilla mapping, Easy for 0, unchanged from the base mod.
  assert.deepEqual([0, 1, 2, 3].map((difficulty) => logic.tierFor(difficulty, 0)), [1, 1, 2, 3]);
  // A random player (story tier 2) raises Easy to Normal and leaves Hard at Hard.
  assert.deepEqual([0, 1, 2, 3].map((difficulty) => logic.tierFor(difficulty, 2)), [2, 2, 2, 3]);
  // Quest opponents and tournaments both raise the policy to Hard (3).
  assert.deepEqual([0, 2, 3].map((difficulty) => logic.tierFor(difficulty, 3)), [3, 3, 3]);
  assert.equal(logic.tierFor(2, 9), 2);
});

test("a blunder is a large fall after a move that left the pick, never at the result", () => {
  assert.equal(logic.isBlunder(move(1, 0.6, 0.35, false), 0.2), true);
  assert.equal(logic.isBlunder(move(1, 0.6, 0.45, false), 0.2), false);
  assert.equal(logic.isBlunder(move(1, 0.6, 0.1, true), 0.2), false);
  assert.equal(logic.isBlunder(move(1, 0.6, 0, false, "pass", "Ves", true), 0.2), false);
  assert.equal(logic.isBlunder(move(1, 0.6, Number.NaN, false), 0.2), false);
});

test("a move matches the pick only in the same slot, side or Decoy target", () => {
  const INVALID_SLOT = -1;
  const INVALID_PLAYER = -1;
  const melee = { kind: "play", instanceId: 7, slotId: 1, targetPlayer: 0 };
  assert.equal(logic.samePlacement(melee, 1, 0, [], INVALID_SLOT, INVALID_PLAYER), true);
  assert.equal(logic.samePlacement(melee, 2, 0, [], INVALID_SLOT, INVALID_PLAYER), false, "an agile card in another row");
  const spy = { kind: "play", instanceId: 7, slotId: 1, targetPlayer: 1 };
  assert.equal(logic.samePlacement(spy, 1, 0, [], INVALID_SLOT, INVALID_PLAYER), false, "the spy on the wrong side");
  const weather = { kind: "play", instanceId: 7, slotId: 9, targetPlayer: INVALID_PLAYER };
  assert.equal(logic.samePlacement(weather, 9, INVALID_PLAYER, [], INVALID_SLOT, INVALID_PLAYER), true);
  const scorch = { kind: "play", instanceId: 7, slotId: INVALID_SLOT, targetPlayer: INVALID_PLAYER };
  assert.equal(logic.samePlacement(scorch, 12, INVALID_PLAYER, [], INVALID_SLOT, INVALID_PLAYER), true);
  const decoy = { kind: "play", instanceId: 7, slotId: INVALID_SLOT, targetInstanceId: 12 };
  assert.equal(logic.samePlacement(decoy, 1, 0, [3, 12], INVALID_SLOT, INVALID_PLAYER), true);
  assert.equal(logic.samePlacement(decoy, 1, 0, [3, 14], INVALID_SLOT, INVALID_PLAYER), false, "a Decoy on another card");
});

test("saved text keeps the user.settings line intact; odd deck names get no per-opponent record", () => {
  assert.equal(logic.settingsText("a=b\r\nc\nd"), "a b  c d");
  assert.equal(logic.settingsText("Ves to melee"), "Ves to melee");
  assert.equal(logic.statsDeckKey("Thaler"), "Thaler");
  assert.equal(logic.statsDeckKey("Mod Deck!"), "");
  assert.equal(logic.statsDeckKey(""), "");
});

test("match stats count the finished match into its tier bucket and opponent", () => {
  assert.equal(logic.outcomeName(true, false), "won");
  assert.equal(logic.outcomeName(false, true), "lost");
  assert.equal(logic.outcomeName(false, false), "drawn");
  assert.equal(logic.tierBucket(false, 3), "vanilla");
  assert.deepEqual([1, 2, 3].map((tier) => logic.tierBucket(true, tier)), ["easy", "normal", "hard"]);
  for (const tier of [0, 4]) assert.throws(() => logic.tierBucket(true, tier), /outside 1..3/);
  assert.equal(logic.bucketLabel("hard"), "Hard");
  assert.equal(logic.bucketLabel("vanilla"), "the vanilla AI");
  assert.equal(logic.safeKey("Crossroads Innkeeper_2!"), "CrossroadsInnkeeper2");
  const counts = { hard_won: 3, hard_lost: 1, deck_won: 0 };
  assert.deepEqual(plain(logic.countsAfter(counts, "hard", "won")), [4, 1, 0]);
  assert.deepEqual(plain(logic.countsAfter(counts, "deck", "drawn")), [0, 0, 1]);
  assert.equal(
    logic.statsLine("Hard", [4, 1, 0], "Thaler", [1, 0, 1]),
    "Record against Hard: 4 won, 1 lost; against Thaler: 1 won, 0 lost, 1 drawn.",
  );
  assert.equal(logic.statsLine("the vanilla AI", [0, 1, 0], "", [0, 0, 0]), "Record against the vanilla AI: 0 won, 1 lost.");
});

test("package manifest records the opt-in feature set and a gated value head", { skip: !BUILD_MANIFEST }, () => {
  const fun = BUILD_MANIFEST.fun_features;
  assert.equal(fun.schema, "w3-gwent-fun-features-v2");
  assert.equal(fun.lab_mode.toggle, "W3GwentAI.GwaiLab");
  assert.equal(fun.lab_mode.default_enabled, false);
  assert.deepEqual(fun.lab_mode.gates, ["meter", "post_game", "blunder", "stats"]);
  assert.equal(fun.reading_network.tier, "hard");
  assert.equal(fun.reading_network.checkpoint_sha256, BUILD_MANIFEST.tier_policies.hard.checkpoint_sha256);
  assert.equal(fun.reading_network.export_sha256, BUILD_MANIFEST.tier_policies.hard.export_sha256);
  assert.equal(fun.default_enabled, false);
  assert.deepEqual(Object.keys(fun.features).sort(), ["blunder", "coach", "meter", "post_game", "stats", "story"]);
  for (const feature of Object.values(fun.features)) {
    assert.equal(feature.default_enabled, false);
    assert.match(feature.toggle, /^W3GwentAI\.Gwai\w+$/);
  }
  assert.equal(fun.story_tiers.categories.tournament.tier, "hard");
  assert.equal(fun.story_tiers.categories.named.tier, "hard");
  assert.equal(fun.story_tiers.categories.generic.tier, "normal");
  for (const policy of BUILD_MANIFEST.policy_exports) {
    assert.equal(policy.value_head.exported, true);
    assert.equal(policy.value_head.node_gate.valuePass, true);
    assert.equal(BUILD_MANIFEST.node_policy_replay[policy.checkpoint_sha256].valuePass, true);
  }
});
