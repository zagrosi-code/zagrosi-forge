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


test('one-shot listeners are cancelled before recursive publication', () => {
  const bus = new EventBus();
  const seen = [];
  const cancel = bus.subscribeOnce('ready', () => {
    seen.push('once');
    assert.equal(bus.listenerCount('ready'), 1);
    assert.equal(cancel(), false);
    assert.equal(bus.publish('ready', {}), 1);
  });
  bus.subscribe('ready', () => seen.push('normal'));
  assert.equal(bus.publish('ready', {}), 2);
  assert.deepEqual(seen, ['once', 'normal', 'normal']);
});

test('delivery snapshots both groups and skips cancelled registrations', () => {
  const bus = new EventBus();
  const seen = [];
  bus.subscribe('ready', () => {
    seen.push('first');
    cancel();
    bus.subscribeAll(() => seen.push('new-global'));
  });
  const cancel = bus.subscribeOnce('ready', () => seen.push('cancelled'));
  assert.equal(bus.publish('ready', {}), 1);
  assert.deepEqual(seen, ['first']);
  assert.equal(bus.listenerCount('ready'), 2);
});

test('a throwing one-shot stays cancelled and stops current delivery', () => {
  const bus = new EventBus();
  const error = new Error('failed');
  const seen = [];
  const cancel = bus.subscribeOnce('ready', () => { throw error; });
  bus.subscribe('ready', () => seen.push('later'));
  assert.throws(() => bus.publish('ready', {}), actual => actual === error);
  assert.equal(cancel(), false);
  assert.deepEqual(seen, []);
  assert.equal(bus.publish('ready', {}), 1);
  assert.deepEqual(seen, ['later']);
});
