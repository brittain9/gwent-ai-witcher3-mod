"use strict";

// GwaiNetWeights.loadStep must build exactly the vector load builds, across any number of calls.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "../as3/GwaiNetWeights.as"), "utf8");

// The flash.utils.ByteArray members the decoder uses.
class ByteArray {
  constructor() {
    this.bytes = [];
    this.position = 0;
    this.endian = "bigEndian";
  }
  writeByte(value) {
    this.bytes.push(value & 255);
  }
  writeUnsignedInt(value) {
    const bytes = Buffer.alloc(4);
    if (this.endian === "littleEndian") bytes.writeUInt32LE(value >>> 0);
    else bytes.writeUInt32BE(value >>> 0);
    this.bytes.push(...bytes);
  }
  get length() {
    return this.bytes.length;
  }
  get bytesAvailable() {
    return this.bytes.length - this.position;
  }
  readFloat() {
    const value = Buffer.from(this.bytes.slice(this.position, this.position + 4));
    this.position += 4;
    return this.endian === "littleEndian" ? value.readFloatLE(0) : value.readFloatBE(0);
  }
}

function loadWeightsClass() {
  const open = SOURCE.indexOf("{", SOURCE.indexOf("internal class GwaiNetWeights"));
  const body = SOURCE.slice(open + 1, SOURCE.lastIndexOf("}"));
  const javascript = body
    .replace(/private static var\s+(\w+)\s*:\s*[\w.<>]+\s*=/g, "let $1 =")
    .replace(/(?:public|private) static function\s+(\w+)\(([^)]*)\)\s*:\s*[\w.<>]+\s*\{/g, "function $1($2) {")
    .replace(/new Vector\.<(?:Number|int)>\(([^,]+),\s*true\)/g, "new Array($1).fill(0)")
    .replace(/new flash\.utils\.ByteArray\(\)/g, "new ByteArray()")
    .replace(/flash\.utils\.Endian\.LITTLE_ENDIAN/g, '"littleEndian"')
    .replace(/flash\.utils\.Endian\.BIG_ENDIAN/g, '"bigEndian"')
    .replace(/\b([A-Za-z_$][\w$]*)\s*:\s*(?:Vector\.<(?:Number|int)>|flash\.utils\.ByteArray|(?:Object|Array|Number|String|Boolean|int|uint|Error)\b(?!\s*\())/g, "$1");
  const moduleShim = { exports: {} };
  vm.runInNewContext(
    `${javascript}\nmodule.exports = { load, loadStep, isLoaded };`,
    { module: moduleShim, ByteArray, Math, String, Error, uint: (value) => Number(value) >>> 0, int: (value) => Math.trunc(Number(value)) | 0 },
  );
  return moduleShim.exports;
}

function embedded(values, checkpoint, chunkFloats) {
  const bytes = Buffer.alloc(values.length * 4);
  values.forEach((value, index) => bytes.writeFloatLE(value, index * 4));
  const chunks = [];
  for (let start = 0; start < values.length; start += chunkFloats) {
    chunks.push(bytes.subarray(start * 4, Math.min(values.length, start + chunkFloats) * 4).toString("base64"));
  }
  return { EMBEDDED: true, CHECKPOINT_SHA256: checkpoint, WEIGHT_COUNT: values.length, BASE64: chunks };
}

const VALUES = Array.from({ length: 2701 }, (_, index) => Math.fround(Math.sin(index) * 3.5 - index / 997));

test("stepwise decoding gives the same weights as load, one chunk per call", () => {
  const reference = loadWeightsClass().load(embedded(VALUES, "a", 300));
  const weights = loadWeightsClass();
  const data = embedded(VALUES, "a", 300);
  assert.equal(data.BASE64.length, 10);
  assert.equal(weights.isLoaded(data), false);
  let calls = 0;
  while (!weights.loadStep(data, 1)) calls++;
  assert.equal(calls, 9, "ten chunks take ten calls");
  assert.equal(weights.isLoaded(data), true);
  const stepped = weights.load(data);
  assert.deepEqual(Array.from(stepped), Array.from(reference));
  assert.equal(stepped[2700], VALUES[2700]);
  assert.equal(weights.loadStep(data, 1), true, "once cached, a step only reports it");
});

test("a full decode by load in the middle of a stepwise decode wins", () => {
  const weights = loadWeightsClass();
  const data = embedded(VALUES, "b", 300);
  assert.equal(weights.loadStep(data, 3), false);
  const loaded = weights.load(data);
  assert.equal(weights.loadStep(data, 1), true);
  assert.equal(weights.load(data), loaded);
});

test("a header that does not match the chunks is refused", () => {
  const weights = loadWeightsClass();
  const data = { ...embedded(VALUES, "c", 300), WEIGHT_COUNT: VALUES.length + 1 };
  assert.throws(() => {
    while (!weights.loadStep(data, 4));
  }, /weight count/);
});
