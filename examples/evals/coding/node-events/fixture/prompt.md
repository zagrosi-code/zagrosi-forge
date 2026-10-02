# One-shot event subscriptions

The dashboard's event bus has topic listeners and global audit observers. Add
`bus.subscribeOnce(topic, listener)` for a caller waiting for the next matching
event. It returns the same kind of cancellation function as `subscribe`.
At the same time, simplify the repeated registration/cancellation and delivery
bookkeeping in `src/events.cjs`. Keep the implementation small and dependency free.

Preserve the existing `EventBus` export and the behavior of `subscribe`,
`subscribeAll`, `publish`, and `listenerCount`. Keep the existing public caller
`src/job-monitor.cjs` unchanged. Add focused tests for the new feature and the
dispatch cases affected by your cleanup. Run `node --test tests/events.test.cjs`.

## Contract

- Topics are non-empty strings, compared exactly (including whitespace and
  Unicode). Invalid topics throw `TypeError('Topic must be a non-empty string')`.
  Invalid listeners throw `TypeError('Listener must be a function')`. Topic
  validation happens first when both arguments are invalid. Failed registration
  has no effect. `subscribeOnce` uses the same validation.
- Every registration is independent, even if a function is registered repeatedly
  or with different subscription methods. Cancellation removes only its own
  registration, returns `true` once, and returns `false` thereafter.
- `publish(topic, payload)` runs synchronously. It takes a snapshot of both the
  matching topic listeners and the global listeners before invoking any callback.
  Topic listeners run first, then global listeners; each group uses registration
  order. A registration cancelled before its turn is skipped. Registrations
  added during publication wait for the next publication; a nested publication
  takes its own fresh snapshot.
- Each callback is called as a plain function with `(payload, topic)`, using the
  original payload reference. The bus does not clone or mutate the payload.
  Callback return values are ignored. `publish` returns the number of callbacks
  invoked when it completes normally. `listenerCount(topic)` includes all active
  listeners for that topic plus all active global observers.
- A one-shot listener is cancelled immediately **before** its callback is invoked.
  It therefore cannot run again during recursive publication, is absent from
  `listenerCount` inside its callback, and stays cancelled if the callback throws.
  Its cancellation function then returns `false`. A one-shot listener that has
  not yet been reached remains registered.
- If any callback throws, propagate that exact error immediately and stop the
  current publication. Ordinary subscriptions remain registered. Global observers
  and later listeners must not run after the error.
- `observeJobs` must keep emitting the same status and audit calls in the same
  order, without mutating its job input. Its returned cleanup must retain its
  existing results. The bus itself performs no logging, I/O, or timer scheduling.
