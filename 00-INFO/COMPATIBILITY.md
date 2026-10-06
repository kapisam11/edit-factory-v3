# Edit Factory V3 compatibility and deprecation policy

## Supported architecture

- Python 3.10+.
- Single-host Flask + SQLite + spawned worker + FFmpeg deployment.
- V3 public pipeline contracts are the preferred integration boundary.

## Legacy compatibility

`VideoDirector` remains available through the 3.x compatibility window, but its use
emits a `DeprecationWarning` and should not be used for new integrations.

Planned deprecation schedule:

- V3.0: compatibility path remains supported and covered by regression tests.
- V3.1: compatibility path remains available but is documented as deprecated.
- V4.0: legacy `VideoDirector` compatibility may be removed after the V3.1 window
  has passed and downstream migrations have been validated.

Do not silently remove or change the legacy API before the deprecation window closes.