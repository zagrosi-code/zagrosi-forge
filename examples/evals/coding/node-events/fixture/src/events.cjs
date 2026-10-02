'use strict';

class EventBus {
  constructor() {
    this._topics = new Map();
    this._all = new Set();
  }

  subscribe(topic, listener) {
    if (typeof topic !== 'string' || topic.length === 0) {
      throw new TypeError('Topic must be a non-empty string');
    }
    if (typeof listener !== 'function') {
      throw new TypeError('Listener must be a function');
    }
    if (!this._topics.has(topic)) this._topics.set(topic, new Set());
    const entries = this._topics.get(topic);
    const entry = { listener, active: true };
    entries.add(entry);
    return () => {
      if (!entry.active) return false;
      entry.active = false;
      entries.delete(entry);
      if (entries.size === 0) this._topics.delete(topic);
      return true;
    };
  }

  subscribeAll(listener) {
    if (typeof listener !== 'function') {
      throw new TypeError('Listener must be a function');
    }
    const entry = { listener, active: true };
    this._all.add(entry);
    return () => {
      if (!entry.active) return false;
      entry.active = false;
      this._all.delete(entry);
      return true;
    };
  }

  publish(topic, payload) {
    if (typeof topic !== 'string' || topic.length === 0) {
      throw new TypeError('Topic must be a non-empty string');
    }
    const topicEntries = Array.from(this._topics.get(topic) || []);
    const allEntries = Array.from(this._all);
    let delivered = 0;
    for (const entry of topicEntries) {
      if (!entry.active) continue;
      const { listener } = entry;
      listener(payload, topic);
      delivered += 1;
    }
    for (const entry of allEntries) {
      if (!entry.active) continue;
      const { listener } = entry;
      listener(payload, topic);
      delivered += 1;
    }
    return delivered;
  }

  listenerCount(topic) {
    if (typeof topic !== 'string' || topic.length === 0) {
      throw new TypeError('Topic must be a non-empty string');
    }
    return (this._topics.get(topic)?.size || 0) + this._all.size;
  }
}

module.exports = { EventBus };
