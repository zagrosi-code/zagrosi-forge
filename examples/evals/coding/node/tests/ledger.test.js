'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { invoice } = require('../src/ledger.js');

test('invoice total', () => {
  assert.equal(invoice('total', [{ price: 101, quantity: 2 }]), 242);
});

test('empty receipt', () => {
  assert.equal(invoice('receipt', [], 'Ada'), 'Customer: Ada\nSubtotal: 0\nTax: 0\nTotal: 0\n');
});
