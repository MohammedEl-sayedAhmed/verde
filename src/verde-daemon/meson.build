daemon_dir = get_option('prefix') / get_option('datadir') / meson.project_name() / 'verde-daemon'

configure_file(
  input: 'main.py',
  output: 'verde-daemon',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

daemon_sources = [
  '__init__.py',
  'audit.py',
  'degraded_states.py',
  'driver_manager.py',
  'nvml_wrapper.py',
  'polkit.py',
  'preflight.py',
  'service.py',
  'validators.py',
]

install_data(daemon_sources, install_dir: daemon_dir)
