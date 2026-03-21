# Test Strategy

## Test Layers

| Layer | Directory | Count | What it covers |
|-------|-----------|-------|----------------|
| Unit | `tests/unit/` | ~1,250 | All daemon modules, GUI components, catalogs |
| Integration | `tests/integration/` | ~25 | D-Bus method dispatch, CLI recovery flows |
| UI | `tests/ui/` | ~20 | GTK widget creation, navigation, driver/rollback flows |
| Security | `tests/security/` | ~20 | No shell=True, systemd sandboxing directives |
| Accessibility | `tests/accessibility/` | 7 | Gettext coverage, RTL CSS, colour independence |

## Running Tests

```bash
# All tests (requires Xvfb for GTK)
PYTHONPATH=src:src/verde-daemon xvfb-run pytest tests/ -v

# Unit only (fast, no display needed)
PYTHONPATH=src:src/verde-daemon pytest tests/unit/ -v

# Security only
PYTHONPATH=src:src/verde-daemon pytest tests/security/ -v

# With coverage
PYTHONPATH=src:src/verde-daemon pytest tests/ --cov=verde --cov=verde_daemon --cov-report=term-missing
```

## Test Patterns

### Daemon module tests
- Injectable dependencies (`run`, `read_file`, `write_file`, `list_confs`)
- Mock subprocess via custom `_run` functions returning `CompletedProcess`
- `tmp_path` fixture for file I/O tests
- No real system access — all subprocess calls mocked

### GUI view tests
- Instantiate widgets directly (no application context needed)
- `MagicMock` for `VerdeDBusClient`
- Test state updates via `_update_status_display()` / `bind_state()`
- Verify widget visibility, labels, CSS classes

### D-Bus wiring tests
- Instantiate `VerdeService` with mocked loop/idle_reset
- Assert `hasattr(svc, "_dispatch_method_name")`
- Verify D-Bus XML contains method declarations
- Verify Polkit `METHOD_ACTION_MAP` completeness

## CI Integration

- **PR checks** (<5 min): ruff + mypy + unit tests + bandit + enforcement rules
- **Merge checks** (<20 min): all PR checks + integration + UI + coverage + .deb build
- **Release**: full test suite + .deb build + GitHub Release
