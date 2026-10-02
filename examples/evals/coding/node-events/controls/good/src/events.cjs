'use strict';

function validateTopic(topic) {
  if (typeof topic !== 'string' || topic.length === 0) {
    throw new TypeError('Topic must be a non-empty string');
  }
}

class EventBus {
  constructor() {
    this._topics = new Map();
    this._all = new Set();
  }

  _register(topic, listener, once) {
    if (typeof listener !== 'function') {
      throw new TypeError('Listener must be a function');
    }
    if (topic !== null && !this._topics.has(topic)) this._topics.set(topic, new Set());
    const entries = topic === null ? this._all : this._topics.get(topic);
    const entry = { listener, once, active: true };
    entry.cancel = () => {
      if (!entry.active) return false;
      entry.active = false;
      entries.delete(entry);
      if (topic !== null && entries.size === 0) this._topics.delete(topic);
      return true;
    };
    entries.add(entry);
    return entry.cancel;
  }

  subscribe(topic, listener) {
    validateTopic(topic);
    return this._register(topic, listener, false);
  }

  subscribeOnce(topic, listener) {
    validateTopic(topic);
    return this._register(topic, listener, true);
  }

  subscribeAll(listener) {
    return this._register(null, listener, false);
  }

  publish(topic, payload) {
    validateTopic(topic);
    const entries = [...(this._topics.get(topic) || []), ...this._all];
    let delivered = 0;
    for (const entry of entries) {
      if (!entry.active) continue;
      if (entry.once) entry.cancel();
      const { listener } = entry;
      listener(payload, topic);
      delivered += 1;
    }
    return delivered;
  }

  listenerCount(topic) {
    validateTopic(topic);
    return (this._topics.get(topic)?.size || 0) + this._all.size;
  }
}

module.exports = { EventBus };
