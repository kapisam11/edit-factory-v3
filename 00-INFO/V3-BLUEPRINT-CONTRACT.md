# V3 Blueprint Contract

## Purpose

The V3 blueprint is the intermediate representation between creative planning and media production. The planner creates it; the renderer consumes its serialized form. Rendering code must not need to know how the creative decision was produced.

```text
USER INPUT -> V3 PLANNER -> VALIDATED BLUEPRINT -> RENDER PLAN -> MEDIA RENDERER -> OUTPUT QC -> FINAL ARTIFACT
```

## Contract rules

- `version` and `schema_version` are `3.0.0`.
- `platform`, `edit_type`, and retention-event kinds are validated against explicit enums/registries.
- Planner-owned collections are frozen at construction time; callers cannot mutate nested blueprint collections in place.
- `V3Blueprint.to_dict()` returns a plain JSON-compatible snapshot.
- `V3Blueprint.from_dict()` rejects missing required fields, malformed typed data, unsupported schema versions, invalid timing, invalid metrics, and invalid editorial data.
- The V3 pipeline writes the blueprint atomically, reads it back, deserializes it, and validates it before media rendering starts.
- The renderer receives a serialized research/render contract rather than the mutable planner object.

## Timing invariants

- Clip indices are contiguous and 1-based.
- Clip start/end values are finite, monotonic, and strictly increasing.
- The final clip end defines the planned duration.
- Music sync points are finite, monotonic, inside the timeline, and end at the planned duration.
- Retention events are finite, monotonic, inside the timeline, and use a supported event kind.

## AI boundary

AI responses are untrusted input. Structured responses are parsed and validated before they are allowed into production state. Invalid deterministic data is not retried indefinitely; it falls back only where the existing pipeline has an explicit deterministic fallback.

## Compatibility

The existing `V3Blueprint` API, `create_v3_blueprint()`, and production renderer remain in place. The contract hardening is incremental: legacy production rendering is still used through the existing V3 renderer bridge.

## Failure behavior

Contract failures are deterministic errors. They should be surfaced before FFmpeg work whenever possible. A failed persisted blueprint must not be rendered.

## Testing

Contract tests cover round-trip serialization, immutability, schema failures, timing failures, and strict model-output parsing. Integration tests remain responsible for real FFmpeg behavior.