# Módulo local do app (não é pacote npm): valores literais no lugar do package.json.
Pod::Spec.new do |s|
  s.name           = 'Tampa'
  s.version        = '1.0.0'
  s.summary        = 'Tampa de privacidade nativa do PigBank'
  s.description    = 'Cobre o app de forma síncrona quando ele perde o foco.'
  s.license        = 'UNLICENSED'
  s.author         = 'PigBank'
  s.homepage       = 'https://pigbankai.com'
  s.platforms      = { :ios => '16.4' }
  s.swift_version  = '5.9'
  s.source         = { git: '' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'

  s.source_files = '*.swift'
  s.resources    = ['tampa_simbolo.png']
  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
    'SWIFT_COMPILATION_MODE' => 'wholemodule'
  }
end
