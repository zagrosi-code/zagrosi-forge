'use strict';
// Independent compatibility oracle outside the editable candidate workspace.
const assert = require('node:assert/strict');
const path = require('node:path');
const baseline = require('../examples/evals/coding/node/src/ledger.js');
const candidate = require(path.resolve(process.argv[2], 'src/ledger.js'));
let seed = 31;
function random(limit) {
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed % limit;
}
const carts = [[], [{ price: 101, quantity: 2 }]];
for (let n = 0; n < 40; n++) {
  carts.push(Array.from({ length: random(6) }, () => ({ price: random(10000), quantity: random(5) })));
}
let assertions = 0;
assert.deepEqual(Object.keys(candidate).sort(), Object.keys(baseline).sort(), 'Public exports changed');
assertions++;
function compare(call, expected, items) {
  const copy = structuredClone(items);
  assert.deepEqual(call(copy), expected);
  assert.deepEqual(copy, items, 'Candidate mutated invoice inputs');
  assertions += 2;
}
for (const items of carts) {
  for (const customer of ['Guest', 'Zoë\nLtd', '']) {
    for (const action of ['total', 'json', 'receipt']) {
      const expected = baseline.invoice(action, structuredClone(items), customer);
      compare(copy => candidate.invoice(action, copy, customer), expected, items);
    }
    compare(copy => new candidate.InvoiceManager().total(copy), new baseline.InvoiceManager().total(structuredClone(items)), items);
    const expected = JSON.parse(baseline.invoice('json', structuredClone(items), customer));
    expected.item_count = items.reduce((count, item) => count + item.quantity, 0);
    compare(copy => candidate.invoice('summary', copy, customer), expected, items);
  }
}
for (const action of ['missing', null, 42]) {
  const items = [];
  assert.throws(() => candidate.invoice(action, items), { constructor: Error, message: 'Unknown action: ' + String(action) });
  assert.deepEqual(items, [], 'Candidate mutated rejected inputs');
  assertions += 2;
}
console.log(JSON.stringify({ case: process.argv[3], assertions }));
