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
  'window.py',
  'views/__init__.py',
  'views/dashboard.py',
  'views/drivers.py',
  'views/power.py',
  'views/diagnostics.py',
]

install_data(verde_sources, install_dir: moduledir, preserve_path: true)
