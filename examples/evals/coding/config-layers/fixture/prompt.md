# Explain the effective service configuration

Support engineers can see final settings but cannot identify which config file or
environment variable supplied them. Add the public function
`explain_config(layers, environ=None)` to `settings` and its `__all__`. Return a
dictionary with exactly `values` and `sources`. `values` must be the same ordered,
detached dictionary returned by `load_config`.

`sources` has the same keys in default order. Each entry is one of these exact
dictionaries:

- Untouched default: `{"kind": "default", "name": null}` (Python `None`).
- Last layer assigning that key: `{"kind": "layer", "name": layer_name}`.
- Explicit environment override: `{"kind": "environment", "name": "APP_PORT"}`
  (use the appropriate variable name).

Repeated layer names are allowed. Assigning the same value still transfers
provenance. A layer's `None` resets the value to its default and retains that
layer as the source. Lists replace; they do not merge. Unknown environment keys
are ignored. No process environment is read when `environ` is omitted.

Preserve `DEFAULTS`, existing functions and `ConfigReader`, their signatures,
error types/messages, validation order, text formatting, and current callers in
`src/service.py`. Config layers may be one-shot iterables: consume them once,
stop at the first invalid layer/key, and do not consult the environment after a
layer failure. Preserve layer `.items()` order and environment lookup order
(`APP_HOST`, `APP_PORT`, `APP_DEBUG`, `APP_TAGS`); do not read an absent variable
or an ignored variable. Returned values and source records must not share mutable
state with caller inputs, defaults, another result, or another source record.

The loader and text renderer duplicate resolution logic. Characterize missing
regressions, then simplify that duplication with cohesive code as part of the
feature. Use Python's standard library, existing unittest discovery, and no new
dependencies. Keep public callers unchanged.
