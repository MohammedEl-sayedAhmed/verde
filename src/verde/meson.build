pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'verde'

configure_file(
  input: 'main.py',
  output: 'verde',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

verde_sources = [
  '__init__.py',
  'dbus_client.py',
  'gpu_state.py',
  'window.py',
  'views/__init__.py',
  'views/dashboard.py',
  'views/drivers.py',
  'views/power.py',
  'views/diagnostics.py',
  'widgets/__init__.py',
  'widgets/status_indicator.py',
  'widgets/driver_card.py',
  'widgets/preflight_banner.py',
  'widgets/progress_overlay.py',
  'widgets/snapshot_row.py',
]

install_data(verde_sources, install_dir: moduledir, preserve_path: true)
