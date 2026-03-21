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
  'apt_errors.py',
  'audit.py',
  'cli_recovery.py',
  'degraded_states.py',
  'diagnostics.py',
  'driver_manager.py',
  'integrity_checker.py',
  'modification_tracker.py',
  'module_doctor.py',
  'nvml_wrapper.py',
  'pending_summary.py',
  'polkit.py',
  'power_manager.py',
  'preflight.py',
  'rate_limiter.py',
  'recovery_diagnostics.py',
  'service.py',
  'snapshot_manager.py',
  'state_tracker.py',
  'sysfs_gpu.py',
  'validators.py',
]

install_data(daemon_sources, install_dir: daemon_dir)
