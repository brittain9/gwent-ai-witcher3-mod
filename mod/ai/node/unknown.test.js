"use strict";

// A card outside the network's vocabulary (a new expansion card, a modded card) must hand the
// turn to the vanilla AI: gwaiBegin asks GwaiNetPolicy.unknownCards before the runner exists.
// These tests inject an unknown template id into a synthetic state, zone by zone, and check that
// the guard names it and that the network itself refuses the state rather than encoding it.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadNetworkPolicy } = require("./replay.js");

const AS3 = path.resolve(__dirname, "../as3");
const UNKNOWN = 424242;
const CARD_IDS = [9001, 9002];
const ROWS = ["melee", "ranged", "siege"];

function zoneCards(observation) {
  const cards = [...observation.hand, ...observation.weather];
  for (const player of observation.players) {
    cards.push(...player.graveyard);
    if (player.leader) cards.push(player.leader);
    for (const row of ROWS) {
      cards.push(...player.rows[row].cards);
      if (player.modifiers[row]) cards.push(player.modifiers[row]);
    }
  }
  return cards;
}

const policy = loadNetworkPolicy(
  fs.readFileSync(path.join(AS3, "GwaiNetPolicy.as"), "utf8"),
  fs.readFileSync(path.join(AS3, "GwaiPolicyCore.as"), "utf8"),
  { card_ids: CARD_IDS, card_effects: CARD_IDS.map(() => []), checkpoint_sha256: "unknown-card-test", parameter_count: 0 },
  [],
);

function syntheticObservation() {
  return {
    seat: 0, initiative: 0, decision_id: 0, round: 1, rounds_played: 0,
    lives: [2, 2], scores: [0, 0], round_results: [],
    hand: [{ instance_id: 1, template_id: 9001, power: 4, score: 4, effects: [] }],
    players: [0, 1].map((seat) => ({
      seat, faction: 0, hand_count: 1, deck_count: 0, passed: false, leader_used: false,
      leader: { instance_id: seat + 2, template_id: 9002, power: 0, score: 0, effects: [] },
      rows: Object.fromEntries(ROWS.map((row) => [row, { cards: [] }])),
      graveyard: [], modifiers: Object.fromEntries(ROWS.map((row) => [row, null])),
    })),
    weather: [], pending_choice: null, terminal: false,
  };
}

function unknownCard(instanceId) {
  return { instance_id: instanceId, template_id: UNKNOWN, power: 4, score: 4, effects: [] };
}

// The 18 zones of observation schema v1, in encoder order, each given one unknown card.
const own = (observation) => observation.players.find((player) => player.seat === observation.seat);
const opponent = (observation) => observation.players.find((player) => player.seat !== observation.seat);
const ZONES = [
  ["own hand", (observation, card) => observation.hand.push(card)],
  ...ROWS.map((row) => [`own ${row} row`, (observation, card) => own(observation).rows[row].cards.push(card)]),
  ...ROWS.map((row) => [`opponent ${row} row`, (observation, card) => opponent(observation).rows[row].cards.push(card)]),
  ["own graveyard", (observation, card) => own(observation).graveyard.push(card)],
  ["opponent graveyard", (observation, card) => opponent(observation).graveyard.push(card)],
  ["weather", (observation, card) => observation.weather.push(card)],
  ["own leader", (observation, card) => { own(observation).leader = card; }],
  ["opponent leader", (observation, card) => { opponent(observation).leader = card; }],
  ...ROWS.map((row) => [`own ${row} modifier`, (observation, card) => { own(observation).modifiers[row] = card; }]),
  ...ROWS.map((row) => [`opponent ${row} modifier`, (observation, card) => { opponent(observation).modifiers[row] = card; }]),
];

test("a synthetic state of known cards hands nothing back and encodes", () => {
  const observation = syntheticObservation();
  assert.deepEqual([...policy.unknownCards(observation, CARD_IDS)], []);
  assert.equal(policy.encodeObservation(observation, CARD_IDS, CARD_IDS.map(() => [])).length, 24 * CARD_IDS.length + 18 * 45 + 64);
});

test("an unknown card in any zone hands the turn back, and the network refuses to encode it", () => {
  assert.equal(ZONES.length, 18);
  for (const [zone, inject] of ZONES) {
    const observation = syntheticObservation();
    inject(observation, unknownCard(9000));
    assert.deepEqual([...policy.unknownCards(observation, CARD_IDS)], [UNKNOWN], zone);
    assert.throws(() => policy.encodeObservation(observation, CARD_IDS, CARD_IDS.map(() => [])),
      /outside the frozen catalog vocabulary/, zone);
  }
});

test("each unknown template id is named once, in observation order", () => {
  const observation = syntheticObservation();
  observation.hand.push(unknownCard(9000), { ...unknownCard(9001), template_id: UNKNOWN + 1 }, unknownCard(9002));
  opponent(observation).graveyard.push(unknownCard(9003));
  assert.deepEqual([...policy.unknownCards(observation, CARD_IDS)], [UNKNOWN, UNKNOWN + 1]);
  observation.hand.push({ instance_id: 9004, power: 1 });
  assert.deepEqual([...policy.unknownCards(observation, CARD_IDS)], [UNKNOWN, UNKNOWN + 1, -1], "a card without a template id is unknown");
});

test("an action naming an unknown card is refused, never encoded as no card", () => {
  const observation = syntheticObservation();
  const visible = zoneCards(observation);
  const known = visible[0];
  assert.deepEqual([...policy.actionFeature({ kind: "pass" }, visible, CARD_IDS)], [0, 0, 0, 0, 0]);
  assert.equal(policy.actionFeature({ kind: "choice", template_id: known.template_id }, visible, CARD_IDS)[4],
    CARD_IDS.indexOf(known.template_id) + 1);
  assert.throws(() => policy.actionFeature({ kind: "choice", template_id: UNKNOWN }, visible, CARD_IDS),
    /outside the frozen catalog vocabulary/);
  visible.push(unknownCard(9000));
  assert.throws(() => policy.actionFeature({ kind: "play", instance_id: 9000, row: "melee" }, visible, CARD_IDS),
    /outside the frozen catalog vocabulary/);
});
