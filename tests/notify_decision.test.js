"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");

function nativeNotification(fallback) {
  const source = fs.readFileSync(
    path.join(ROOT, "shared_player/host_hooks.py"),
    "utf8",
  );
  const match = source.match(
    /public function gwaiNotifyDecision\([^)]*\)\s*:\s*void\s*\{([\s\S]*?)\n      \}\n\n      public function gwaiRecordDecision/,
  );
  assert.ok(match, "execute the actual authored native notification method");
  const body = match[1].replace(/var (\w+):String/g, "var $1");
  const notify = vm.runInNewContext(
    `(function(profile,fallback,detail="") {${body}\n})`,
  );
  const settings = fs.readFileSync(
    path.join(ROOT, "mod/ai/settings/scripts/local/gwai_settings.ws"),
    "utf8",
  );
  const sink = settings.slice(settings.indexOf("event OnW3GwentAIDiagnostic("));
  const categories = [...sink.matchAll(/kind != "([a-z]+)"/g)].map(
    (entry) => entry[1],
  );
  assert.deepEqual(categories, ["network", "greedy", "vanilla", "stock"]);
  const counts = Object.fromEntries(categories.map((kind) => [kind, 0]));
  const records = [];
  const receiver = {
    _gwaiSettings: { signal: false },
    gwaiRecordDecision(kind, detail) {
      records.push({ kind, detail });
      if (Object.hasOwn(counts, kind)) counts[kind]++;
    },
  };
  notify.call(receiver, 2, fallback, "registered decision detail");
  return { counts, records };
}

test("shared neural decision reaches the existing native network counter once", () => {
  const { counts, records } = nativeNotification(false);
  assert.equal(counts.network, 1);
  assert.equal(counts.vanilla, 0);
  assert.deepEqual(records, [
    { kind: "network", detail: "registered decision detail" },
  ]);
});

test("native fallback keeps the existing vanilla category and detail once", () => {
  const { counts, records } = nativeNotification(true);
  assert.equal(counts.network, 0);
  assert.equal(counts.vanilla, 1);
  assert.deepEqual(records, [
    { kind: "vanilla", detail: "registered decision detail" },
  ]);
});
