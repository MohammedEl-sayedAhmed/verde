project(
  'verde',
  version: '0.1.0',
  meson_version: '>= 0.64.0',
  default_options: ['warning_level=2'],
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

py_installation = python.find_installation('python3')

profile = get_option('profile')
if profile == 'development'
  app_id = 'com.verde.app.Devel'
else
  app_id = 'com.verde.app'
endif

conf = configuration_data()
conf.set('APP_ID', app_id)
conf.set('VERSION', meson.project_version())
conf.set('PREFIX', get_option('prefix'))
conf.set('DATADIR', get_option('prefix') / get_option('datadir'))
conf.set('BINDIR', get_option('prefix') / get_option('bindir'))
conf.set('PYTHON', py_installation.full_path())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', get_option('prefix') / get_option('datadir') / meson.project_name())

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  update_desktop_database: true,
)
