pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

# Blueprint compilation (.blp → .ui)
blueprint_compiler = find_program('blueprint-compiler', required: false)
if blueprint_compiler.found()
  blueprint_sources = files(
    'ui/window.blp',
    'ui/dashboard_page.blp',
    'ui/drivers_page.blp',
    'ui/power_page.blp',
    'ui/diagnostics_page.blp',
  )

  blueprint_outputs = [
    'window.ui',
    'dashboard_page.ui',
    'drivers_page.ui',
    'power_page.ui',
    'diagnostics_page.ui',
  ]

  blueprints = custom_target(
    'blueprints',
    input: blueprint_sources,
    output: blueprint_outputs,
    command: [
      blueprint_compiler,
      'batch-compile',
      '@OUTDIR@',
      '@CURRENT_SOURCE_DIR@/ui',
      '@INPUT@',
    ],
  )

  # GResource bundle (compiled UI files + CSS)
  gnome.compile_resources(
    'verde',
    'verde.gresource.xml',
    gresource_bundle: true,
    install: true,
    install_dir: pkgdatadir,
    dependencies: blueprints,
    source_dir: ['.'],
  )
else
  warning('blueprint-compiler not found — building without Blueprint UI compilation')
  # GResource bundle (CSS only — no compiled UI files)
  gnome.compile_resources(
    'verde',
    'verde.gresource.xml.no-blp',
    gresource_bundle: true,
    install: true,
    install_dir: pkgdatadir,
  )
endif

# Desktop file
msgfmt = find_program('msgfmt', required: false)
if msgfmt.found()
  desktop_file = i18n.merge_file(
    input: 'com.verde.app.desktop.in',
    output: 'com.verde.app.desktop',
    type: 'desktop',
    po_dir: '../po',
    install: true,
    install_dir: get_option('datadir') / 'applications',
  )
else
  # Fallback: install untranslated desktop file
  install_data(
    'com.verde.app.desktop.in',
    rename: 'com.verde.app.desktop',
    install_dir: get_option('datadir') / 'applications',
  )
  desktop_file = files('com.verde.app.desktop.in')
endif

# Validate desktop file if desktop-file-validate is available
desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

# AppStream metadata
if msgfmt.found()
  appstream_file = i18n.merge_file(
    input: 'com.verde.app.metainfo.xml.in',
    output: 'com.verde.app.metainfo.xml',
    po_dir: '../po',
    install: true,
    install_dir: get_option('datadir') / 'metainfo',
  )
else
  install_data(
    'com.verde.app.metainfo.xml.in',
    rename: 'com.verde.app.metainfo.xml',
    install_dir: get_option('datadir') / 'metainfo',
  )
  appstream_file = files('com.verde.app.metainfo.xml.in')
endif

# Validate AppStream if appstreamcli is available
appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test('Validate appstream file', appstreamcli,
    args: ['validate', '--no-net', '--explain', appstream_file])
endif

# GSettings schema
install_data(
  'com.verde.app.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas',
)

# App icon
install_data(
  'icons/hicolor/scalable/apps/com.verde.app.svg',
  install_dir: get_option('datadir') / 'icons' / 'hicolor' / 'scalable' / 'apps',
)

# D-Bus service activation file
dbus_service_conf = configuration_data()
dbus_service_conf.set('BINDIR', get_option('prefix') / get_option('bindir'))

configure_file(
  input: 'com.verde.Manager.service.in',
  output: 'com.verde.Manager.service',
  configuration: dbus_service_conf,
  install: true,
  install_dir: get_option('datadir') / 'dbus-1' / 'system-services',
)

# D-Bus bus policy
install_data(
  'com.verde.Manager.conf',
  install_dir: get_option('datadir') / 'dbus-1' / 'system.d',
)

# D-Bus introspection XML
install_data(
  'com.verde.Manager.xml',
  install_dir: pkgdatadir,
)

# Polkit policy
install_data(
  'com.verde.policy',
  install_dir: get_option('datadir') / 'polkit-1' / 'actions',
)

# systemd service unit
configure_file(
  input: 'com.verde.Manager.systemd.service.in',
  output: 'verde-daemon.service',
  configuration: dbus_service_conf,
  install: true,
  install_dir: get_option('prefix') / 'lib' / 'systemd' / 'system',
)
