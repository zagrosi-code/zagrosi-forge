'use strict';

// Independent public-behavior checks. No candidate source inspection or LOC gate.
const assert = require('node:assert/strict');
const path = require('node:path');
const workspace = process.argv[2];
const caseId = process.argv[3];
if (!workspace || !caseId) throw new Error('Usage: node event_trial_checks.cjs WORKSPACE CASE');

const effects = [];
const saved = [];
for (const [object, names] of [
  [console, ['log', 'info', 'warn', 'error', 'debug']],
  [globalThis, ['setTimeout', 'setInterval', 'setImmediate', 'queueMicrotask']],
]) {
  for (const name of names) {
    saved.push([object, name, object[name]]);
    object[name] = (...args) => { effects.push(name); return undefined; };
  }
}

let assertions = 0;
function equal(actual, expected, message) {
  assertions += 1;
  assert.deepEqual(actual, expected, message);
}
function identical(actual, expected, message) {
  assertions += 1;
  assert.strictEqual(actual, expected, message);
}
function throws(fn, expected, message) {
  assertions += 1;
  assert.throws(fn, expected, message);
}

try {
  const { EventBus } = require(path.resolve(workspace, 'src/events.cjs'));
  const { observeJobs } = require(path.resolve(workspace, 'src/job-monitor.cjs'));
  identical(typeof EventBus, 'function', 'Preserve the EventBus export');
  identical(typeof new EventBus().subscribeOnce, 'function', 'Add subscribeOnce');

  // Exact topic matching, ordering, payload identity, plain invocation and counts.
  {
    const bus = new EventBus();
    const seen = [];
    const payload = { id: 'job-7', nested: { status: 'ready' } };
    const copy = structuredClone(payload);
    equal(bus.publish('empty', payload), 0);
    equal(bus.listenerCount('empty'), 0);
    const observer = name => function (value, topic) {
      identical(this, undefined, 'Invoke callbacks as plain functions');
      identical(value, payload, 'Pass the original payload');
      seen.push([name, topic]);
    };
    bus.subscribeAll(observer('all-first'));
    bus.subscribe('résumé ', observer('topic-first'));
    bus.subscribe('résumé', observer('different-topic'));
    bus.subscribeOnce('résumé ', observer('once'));
    bus.subscribeAll(observer('all-last'));
    bus.subscribe('résumé ', observer('topic-last'));
    equal(bus.listenerCount('résumé '), 5);
    equal(bus.publish('résumé ', payload), 5);
    equal(seen, [
      ['topic-first', 'résumé '], ['once', 'résumé '], ['topic-last', 'résumé '],
      ['all-first', 'résumé '], ['all-last', 'résumé '],
    ]);
    equal(bus.listenerCount('résumé '), 4);
    equal(bus.listenerCount('résumé'), 3);
    equal(payload, copy, 'The bus does not mutate its payload');
  }

  // Repeated registrations have separate identity and cancellation functions.
  for (const method of ['subscribe', 'subscribeOnce', 'subscribeAll']) {
    const bus = new EventBus();
    const seen = [];
    const listener = value => seen.push(value);
    const register = () => method === 'subscribeAll'
      ? bus[method](listener) : bus[method]('x', listener);
    const first = register();
    const second = register();
    const third = register();
    equal(bus.listenerCount('x'), 3, `${method}: keep repeated registrations`);
    equal(first(), true);
    equal(first(), false);
    equal(bus.listenerCount('x'), 2);
    equal(bus.publish('x', 3), 2);
    equal(seen, [3, 3]);
    equal(second(), method !== 'subscribeOnce');
    equal(third(), method !== 'subscribeOnce');
    equal(second(), false);
    equal(bus.publish('x', 4), 0);
  }
  {
    const bus = new EventBus();
    let calls = 0;
    const listener = () => { calls += 1; };
    bus.subscribe('x', listener);
    bus.subscribeOnce('x', listener);
    bus.subscribeAll(listener);
    equal(bus.publish('x', {}), 3);
    equal(bus.publish('x', {}), 2);
    equal(calls, 5, 'Subscription methods do not collapse callback identity');
  }

  // Invalid registration has no side effects; topic errors take precedence.
  for (const topic of [undefined, null, 0, '', {}, [], Symbol('topic')]) {
    const bus = new EventBus();
    const topicError = { constructor: TypeError, message: 'Topic must be a non-empty string' };
    for (const method of ['subscribe', 'subscribeOnce']) {
      throws(() => bus[method](topic, null), topicError);
      throws(() => bus[method](topic, () => {}), topicError);
    }
    throws(() => bus.publish(topic, {}), topicError);
    throws(() => bus.listenerCount(topic), topicError);
    equal(bus.listenerCount('valid'), 0);
  }
  for (const listener of [undefined, null, 1, 'callback', {}, []]) {
    const bus = new EventBus();
    const listenerError = { constructor: TypeError, message: 'Listener must be a function' };
    throws(() => bus.subscribe('valid', listener), listenerError);
    throws(() => bus.subscribeOnce('valid', listener), listenerError);
    throws(() => bus.subscribeAll(listener), listenerError);
    equal(bus.listenerCount('valid'), 0);
  }
  for (const topic of [' ', '__proto__', 'constructor', '事件', 'x\u0000y']) {
    const bus = new EventBus();
    let received;
    bus.subscribeOnce(topic, (_, actual) => { received = actual; });
    equal(bus.publish(topic, null), 1);
    identical(received, topic);
    equal(bus.publish(topic, null), 0);
  }

  // Both groups are snapshotted before delivery; cancellations remain effective.
  {
    const bus = new EventBus();
    const seen = [];
    let setup = true;
    let stopLater;
    let stopGlobal;
    bus.subscribe('x', () => {
      seen.push('first');
      if (setup) {
        setup = false;
        equal(stopLater(), true);
        equal(stopGlobal(), true);
        bus.subscribe('x', () => seen.push('new-topic'));
        bus.subscribeAll(() => seen.push('new-global'));
      }
    });
    stopLater = bus.subscribeOnce('x', () => seen.push('cancelled-topic'));
    stopGlobal = bus.subscribeAll(() => seen.push('cancelled-global'));
    equal(bus.publish('x', {}), 1);
    equal(seen, ['first']);
    equal(bus.listenerCount('x'), 3);
    equal(bus.publish('x', {}), 3);
    equal(seen, ['first', 'first', 'new-topic', 'new-global']);
  }
  {
    const bus = new EventBus();
    const seen = [];
    let nested = false;
    bus.subscribe('x', () => {
      seen.push(nested ? 'inner-first' : 'outer-first');
      if (!nested) {
        nested = true;
        bus.subscribe('x', () => seen.push('new-topic'));
        bus.subscribeAll(() => seen.push('new-global'));
        equal(bus.publish('x', {}), 4);
      }
    });
    bus.subscribeAll(() => seen.push('old-global'));
    equal(bus.publish('x', {}), 2);
    equal(seen, ['outer-first', 'inner-first', 'new-topic', 'old-global', 'new-global', 'old-global']);
  }
  {
    const bus = new EventBus();
    const seen = [];
    let stop;
    stop = bus.subscribe('x', () => { seen.push('self'); stop(); });
    bus.subscribe('x', () => seen.push('other'));
    equal(bus.publish('x', {}), 2);
    equal(bus.publish('x', {}), 1);
    equal(seen, ['self', 'other', 'other']);
    equal(stop(), false);
  }

  // Once is consumed before user code, including recursive publication.
  {
    const bus = new EventBus();
    const seen = [];
    let nested = false;
    let cancel;
    cancel = bus.subscribeOnce('x', () => {
      seen.push('once');
      equal(bus.listenerCount('x'), 1, 'Once is already absent inside its callback');
      equal(cancel(), false, 'Once cancellation already took effect');
      if (!nested) {
        nested = true;
        equal(bus.publish('x', {}), 1);
      }
    });
    bus.subscribe('x', () => seen.push('normal'));
    equal(bus.publish('x', {}), 2);
    equal(seen, ['once', 'normal', 'normal']);
    equal(bus.publish('x', {}), 1);
    equal(cancel(), false);
  }
  {
    const bus = new EventBus();
    let count = 0;
    const listener = () => {
      count += 1;
      if (count === 1) bus.subscribeOnce('x', listener);
    };
    bus.subscribeOnce('x', listener);
    equal(bus.publish('x', {}), 1);
    equal(bus.listenerCount('x'), 1);
    equal(bus.publish('x', {}), 1);
    equal(bus.publish('x', {}), 0);
    equal(count, 2, 'A callback can independently subscribe itself again');
  }
  {
    const bus = new EventBus();
    const seen = [];
    let nested = false;
    bus.subscribe('x', () => {
      seen.push('normal');
      if (!nested) { nested = true; equal(bus.publish('x', {}), 2); }
    });
    bus.subscribeOnce('x', () => seen.push('once'));
    equal(bus.publish('x', {}), 1, 'Outer snapshot skips a once consumed by inner publication');
    equal(seen, ['normal', 'normal', 'once']);
  }

  // Preserve exact thrown values, short circuiting, and reached/unreached state.
  for (const error of [new Error('listener failed'), { kind: 'listener failure' }, null]) {
    const bus = new EventBus();
    const seen = [];
    const stop = bus.subscribeOnce('x', () => { seen.push('once'); throw error; });
    bus.subscribe('x', () => seen.push('later'));
    bus.subscribeAll(() => seen.push('global'));
    throws(() => bus.publish('x', {}), actual => actual === error);
    equal(seen, ['once']);
    equal(stop(), false);
    equal(bus.listenerCount('x'), 2);
    equal(bus.publish('x', {}), 2);
    equal(seen, ['once', 'later', 'global']);
  }
  {
    const bus = new EventBus();
    const error = new Error('before once');
    const seen = [];
    const stop = bus.subscribe('x', () => { throw error; });
    bus.subscribeOnce('x', () => seen.push('once'));
    bus.subscribeAll(() => seen.push('global'));
    throws(() => bus.publish('x', {}), actual => actual === error);
    equal(bus.listenerCount('x'), 3, 'Unreached once and throwing normal listener remain active');
    equal(seen, []);
    equal(stop(), true);
    equal(bus.publish('x', {}), 2);
    equal(seen, ['once', 'global']);
    equal(bus.publish('x', {}), 1);
  }
  {
    const bus = new EventBus();
    const payload = { value: 1 };
    const ignored = { get then() { throw new Error('Callback return values must be ignored'); } };
    bus.subscribe('x', value => { value.value += 1; return ignored; });
    bus.subscribeOnce('x', value => { identical(value, payload); equal(value.value, 2); return false; });
    equal(bus.publish('x', payload), 2);
    equal(payload.value, 2, 'Listener mutations remain visible to later listeners and caller');
  }

  // Existing caller still emits the same interleaved effects and cleanup results.
  {
    const bus = new EventBus();
    const seen = [];
    const job = { id: 'job-42', status: 'ready' };
    const copy = structuredClone(job);
    const stop = observeJobs(bus,
      (...args) => seen.push(['status', ...args]),
      row => seen.push(['audit', row]));
    equal(bus.publish('job:changed', job), 2);
    equal(bus.publish('job:queued', job), 1);
    equal(seen, [
      ['status', 'job-42', 'ready'],
      ['audit', { topic: 'job:changed', id: 'job-42' }],
      ['audit', { topic: 'job:queued', id: 'job-42' }],
    ]);
    equal(job, copy);
    equal(stop(), [true, true]);
    equal(stop(), [false, false]);
    equal(bus.publish('job:changed', job), 0);
    equal(bus.listenerCount('job:changed'), 0);
  }
  equal(effects, [], 'The bus performs no logging or scheduling');
} finally {
  for (const [object, name, original] of saved) object[name] = original;
}

console.log(JSON.stringify({ case: caseId, assertions }));
