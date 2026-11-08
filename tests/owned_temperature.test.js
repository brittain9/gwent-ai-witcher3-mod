"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function sampler(globalRandom) {
  const source = fs.readFileSync(
    path.join(__dirname, "../mod/ai/as3/GwaiNetPolicy.as"),
    "utf8",
  );
  function extracted(name) {
    const pattern = new RegExp(`(?:public|private) static function ${name}\\(`);
    const match = pattern.exec(source);
    assert.ok(match, `authored method ${name} missing`);
    const start = match.index;
    const opening = source.indexOf("{", start);
    let depth = 1;
    let end = opening + 1;
    for (; end < source.length; end++) {
      if (source[end] === "{") depth++;
      else if (source[end] === "}" && --depth === 0) break;
    }
    const declaration = source
      .slice(start, opening)
      .replace(/(?:public|private) static function/, "function")
      .replace(/\)\s*:\s*(?:int|Object|Array)\s*$/, ") ")
      .replace(/:\s*(?:Number|Array|Object|int)(?=[,\s)=])/g, "");
    const body = source
      .slice(opening, end + 1)
      .replace(/:\s*(?:Number|Array|Object|int)(?=[,\s)=;])/g, "")
      .replace(/\bint\s*\(/g, "Math.trunc(");
    return declaration + body;
  }
  const body =
    extracted("ownedActionUniforms") +
    extracted("temperatureWeights") +
    extracted("sampleLogit");
  const math = Object.create(Math);
  math.random = globalRandom;
  const context = { Math: math };
  vm.runInNewContext(
    body +
      ";this.sample=sampleLogit;this.weights=temperatureWeights;this.uniforms=ownedActionUniforms;",
    context,
  );
  context.sample.weights = context.weights;
  context.sample.uniforms = context.uniforms;
  return context.sample;
}

test("zero-uniform never selects a zero-weight softmax action under underflow", () => {
  const sample = sampler(() => 0);
  assert.equal(sample([-1000, 0], [0, 1], 1, 0), 1);
});
test("explicit owned uniform is the only sampling input; global RNG remains untouched", () => {
  const sample = sampler(() => {
    throw new Error("live Math.random consumed");
  });
  assert.equal(sample([0, 0], [0, 1], 1, 0.9), 1);
});

test("actual AS3 weights and seeded draws match Python group/conditional/joint laws", () => {
  const child = require("node:child_process");
  const root = path.resolve(__dirname, "..");
  const cases = [
    { g: [0, 0], l: [0, 0, 0], ids: [0, 0, 1], t: 1, u: [0, 0.5] },
    { g: [0.1, 0], l: [0, 0, 20], ids: [0, 0, 1], t: 0.25, u: [0.9, 0.2] },
    { g: [-1000, 0], l: [-1000, 0, 0], ids: [0, 0, 1], t: 1, u: [0, 0] },
    {
      g: [3, -2, 1],
      l: [-2, 4, 0, 1, -3],
      ids: [0, 0, 1, 2, 2],
      t: 0.1,
      u: [0.999999, 0.12345],
    },
  ];
  const script = `import json,sys
from shared_player.difficulty import select_temperature
class Draws:
 def __init__(self,values):self.values=iter(values)
 def random(self):return next(self.values)
records=json.loads(sys.argv[1])
print(json.dumps([select_temperature(r['g'],r['l'],r['ids'],temperature=r['t'],rng=Draws(r['u'])) for r in records],allow_nan=False))
`;
  const expected = JSON.parse(
    child.execFileSync(
      process.env.PYTHON || "python",
      ["-B", "-S", "-c", script, JSON.stringify(cases)],
      {
        encoding: "utf8",
        env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: "1" },
      },
    ),
  );
  const sample = sampler(() => {
    throw new Error("live RNG touched");
  });
  function close(a, b) {
    assert.equal(a.length, b.length);
    for (let i = 0; i < a.length; i++)
      assert.ok(
        Math.abs(a[i] - b[i]) <= 1e-12,
        `probability ${i}: ${a[i]} vs ${b[i]}`,
      );
  }
  for (let i = 0; i < cases.length; i++) {
    const r = cases[i],
      e = expected[i],
      groups = r.g.map((_, k) => k);
    const pg = Array.from(sample.weights(r.g, groups, r.t).probabilities);
    const group = sample(r.g, groups, r.t, r.u[0]);
    const conditional = groups.map(() => new Array(r.l.length).fill(0));
    for (const g of groups) {
      const members = r.ids.flatMap((value, k) => (value === g ? [k] : []));
      const pc = sample.weights(r.l, members, r.t).probabilities;
      members.forEach((action, k) => {
        conditional[g][action] = pc[k];
      });
      close(conditional[g], e.conditional_probabilities[g]);
    }
    const members = r.ids.flatMap((value, k) => (value === group ? [k] : []));
    const chosen = sample(r.l, members, r.t, r.u[1]);
    close(pg, e.group_probabilities);
    close(
      r.ids.map((g, k) => pg[g] * conditional[g][k]),
      e.joint_probabilities,
    );
    assert.equal(group, e.chosen_group);
    assert.equal(chosen, e.chosen_action);
  }
});

test("authored AS3 and Python owned uint32 RNG yield identical uniform pairs", () => {
  const child = require("node:child_process"),
    root = path.resolve(__dirname, ".."),
    seeds = [0, 1, 7, 4294967295];
  const script = `import json,sys
from shared_player.difficulty import OwnedActionRng
rows=[]
for seed in json.loads(sys.argv[1]):
 rng=OwnedActionRng(seed)
 rows.append([rng.random(),rng.random()])
print(json.dumps(rows))
`;
  const expected = JSON.parse(
    child.execFileSync(
      process.env.PYTHON || "python",
      ["-B", "-S", "-c", script, JSON.stringify(seeds)],
      {
        encoding: "utf8",
        env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: "1" },
      },
    ),
  );
  const sample = sampler(() => {
    throw new Error("live RNG touched");
  });
  seeds.forEach((seed, index) =>
    assert.deepEqual(Array.from(sample.uniforms(seed)), expected[index]),
  );
  for (const seed of [-1, 1.5, 4294967296, NaN, Infinity, true, null, "7"])
    assert.throws(() => sample.uniforms(seed));
});

test("full authored inference consumes uniforms in group-then-local order and Hard stays exact", () => {
  const child = require("node:child_process"),
    root = path.resolve(__dirname, "..");
  const script = `import json,sys,struct,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/tests')
from test_shared_player_bundle import synthetic_export
with tempfile.TemporaryDirectory() as directory:
 path=synthetic_export(Path(directory)/'export')
 metadata=json.loads((path/'metadata.json').read_text())
 wire=(path/'policy.f32.bin').read_bytes()
 print(json.dumps({'metadata':metadata,'weights':list(struct.unpack('<'+'f'*(len(wire)//4),wire))}))
`;
  const fixture = JSON.parse(
    child.execFileSync(
      process.env.PYTHON || "python",
      ["-B", "-S", "-c", script, root],
      {
        encoding: "utf8",
        env: { ...process.env, PYTHONPATH: root, PYTHONDONTWRITEBYTECODE: "1" },
        maxBuffer: 2 * 1024 * 1024,
      },
    ),
  );
  const { loadPolicy } = require("../mod/ai/node/shared_numeric.js");
  const policy = loadPolicy(
    fs.readFileSync(path.join(root, "mod/ai/as3/GwaiNetPolicy.as"), "utf8"),
  );
  const player = (seat) => ({
    seat,
    faction: 1,
    hand_count: seat === 1 ? 1 : 0,
    deck_count: 1,
    leader: null,
    leader_used: false,
    graveyard: [],
    rows: { melee: { cards: [] }, ranged: { cards: [] }, siege: { cards: [] } },
    modifiers: { melee: null, ranged: null, siege: null },
    passed: false,
  });
  const view = {
    seat: 1,
    initiative: 1,
    decision_id: 0,
    round: 1,
    rounds_played: 0,
    lives: [2, 2],
    scores: [0, 0],
    round_results: [],
    hand: [
      {
        instance_id: 10,
        template_id: 9001,
        power: 4,
        score: 4,
        effects: [],
        types: {
          creature: true,
          hero: false,
          spy: false,
          melee: true,
          ranged: true,
          siege: false,
          weather: false,
          rowModifier: false,
        },
      },
    ],
    players: [player(0), player(1)],
    weather: [],
    pending_choice: null,
    terminal: false,
  };
  const legal = [
    { id: "play:10:melee", kind: "play", instance_id: 10, row: "melee" },
    { id: "play:10:ranged", kind: "play", instance_id: 10, row: "ranged" },
    { id: "pass", kind: "pass" },
  ];
  const m = fixture.metadata;
  const args = [
    view,
    legal,
    m.card_ids,
    m.card_effects,
    fixture.weights,
    m.offsets,
    m.width,
    m.blocks,
  ];
  const hard = policy.evaluate(...args, 0, "full-faction-v1");
  assert.equal(hard.chosen_index, 0);
  const sample = policy.evaluate(...args, 1, "full-faction-v1", [0.8, 0.2]);
  assert.equal(
    sample.chosen_index,
    2,
    "group uniform .8 selects pass, not local draw .2",
  );
  assert.deepEqual(
    Array.from(sample.group_logits),
    Array.from(hard.group_logits),
  );
  assert.deepEqual(
    Array.from(sample.local_logits),
    Array.from(hard.local_logits),
  );
  assert.equal(sample.value, hard.value);
  assert.throws(() => policy.evaluate(...args, 1, "full-faction-v1"));
  assert.throws(() =>
    policy.evaluate(...args, 1, "full-faction-v1", [true, 0.5]),
  );
});

test("authored AS3 sampler requires the explicit fourth uniform at every native caller", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "../mod/ai/as3/GwaiNetPolicy.as"),
    "utf8",
  );
  const declaration = source.match(
    /public static function sampleLogit\(([^)]*)\)/,
  );
  assert.ok(declaration);
  assert.equal(declaration[1].split(",")[3].trim(), "uniform:Number");
  const callers = source
    .split("\n")
    .filter(
      (line) => line.includes("sampleLogit(") && !line.includes("function"),
    );
  assert.equal(callers.length, 2);
  for (const caller of callers) {
    const argumentsText = caller.match(/sampleLogit\((.*)\)/)[1];
    assert.equal(argumentsText.split(",").length, 4);
    assert.match(
      argumentsText.split(",")[3],
      /Number\(actionUniforms\[[01]\]\)/,
    );
  }
});
