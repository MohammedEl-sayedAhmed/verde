# Contributing to Verde

## Prerequisites

- Ubuntu 24.04 (Noble Numbat)
- System packages:
  ```bash
  sudo apt install libgtk-4-dev libadwaita-1-dev blueprint-compiler \
    python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
    meson ninja-build gettext desktop-file-utils appstream
  ```
- Python tools:
  ```bash
  pip install ruff mypy pytest pytest-cov
  ```

## Development Setup

```bash
git clone https://github.com/verde-gpu/verde.git
cd verde
meson setup builddir
meson compile -C builddir
```

## Running Tests

Verde uses a layered test strategy:

```bash
# Unit tests (fast, no system access)
PYTHONPATH=src:src/verde-daemon pytest tests/unit/ -v

# Security tests (static analysis)
PYTHONPATH=src:src/verde-daemon pytest tests/security/ -v

# Accessibility tests (gettext, RTL, colour)
PYTHONPATH=src:src/verde-daemon pytest tests/accessibility/ -v

# Integration tests (require system D-Bus — use sudo for some)
PYTHONPATH=src:src/verde-daemon pytest tests/integration/ -v

# UI smoke tests (require Xvfb)
PYTHONPATH=src:src/verde-daemon xvfb-run pytest tests/ui/ -v

# All tests
PYTHONPATH=src:src/verde-daemon xvfb-run pytest tests/ -v
```

## Code Style

- **Formatter:** `ruff format src/ tests/`
- **Linter:** `ruff check src/ tests/`
- **Type checker:** `mypy src/verde/ --ignore-missing-imports`

## Architecture

Verde uses a strict GUI/daemon split:

- `src/verde/` — GTK4/Libadwaita GUI (runs as user)
- `src/verde-daemon/` — System daemon (runs as root via systemd)
- Communication: D-Bus system bus (`com.verde.Manager`)
- Authorization: Polkit for privileged operations

**Key rule:** GUI code MUST NOT import daemon code or access system resources directly. All system access goes through D-Bus.

## PR Workflow

1. Create a feature branch from `main`
2. Make changes, write tests
3. Run `ruff check && ruff format --check && mypy src/verde/`
4. Run `PYTHONPATH=src:src/verde-daemon pytest tests/unit/ tests/security/`
5. Push and open a PR — CI runs automatically
6. Address review feedback
7. Merge when CI passes and review is approved

## Building .deb Package

```bash
sudo apt install debhelper devscripts lintian
debuild -us -uc -b
lintian --fail-on error ../*.changes
```

## Release Process

1. Update version in `meson.build`
2. Update `debian/changelog`
3. Commit, tag: `git tag v1.0.0`
4. Push tag: `git push origin v1.0.0`
5. CI builds .deb and creates GitHub Release automatically
