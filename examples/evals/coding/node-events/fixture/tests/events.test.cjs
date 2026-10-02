'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventBus } = require('../src/events.cjs');
const { observeJobs } = require('../src/job-monitor.cjs');

test('delivers a topic and can unsubscribe', () => {
  const bus = new EventBus();
  const seen = [];
  const stop = bus.subscribe('ready', payload => seen.push(payload.value));
  assert.equal(bus.publish('other', { value: 1 }), 0);
  assert.equal(bus.publish('ready', { value: 2 }), 1);
  assert.deepEqual(seen, [2]);
  assert.equal(stop(), true);
  assert.equal(stop(), false);
  assert.equal(bus.publish('ready', { value: 3 }), 0);
});

test('global subscriptions receive the topic', () => {
  const bus = new EventBus();
  const seen = [];
  const stop = bus.subscribeAll((payload, topic) => seen.push([topic, payload.value]));
  assert.equal(bus.publish('ready', { value: 2 }), 1);
  assert.deepEqual(seen, [['ready', 2]]);
  stop();
});

test('job monitor connects the status and audit callbacks', () => {
  const bus = new EventBus();
  const statuses = [];
  const audit = [];
  const stop = observeJobs(bus, (...args) => statuses.push(args), row => audit.push(row));
  bus.publish('job:changed', { id: 'j7', status: 'ready' });
  assert.deepEqual(statuses, [['j7', 'ready']]);
  assert.deepEqual(audit, [{ topic: 'job:changed', id: 'j7' }]);
  assert.deepEqual(stop(), [true, true]);
});

test('rejects empty topics and non-callable listeners', () => {
  const bus = new EventBus();
  assert.throws(() => bus.subscribe('', () => {}), TypeError);
  assert.throws(() => bus.subscribe('ready', null), TypeError);
  assert.throws(() => bus.subscribeAll(null), TypeError);
});
