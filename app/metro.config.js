// Config do Metro via `getSentryExpoConfig`: mesma coisa que `getDefaultConfig`
// do Expo, mas injeta o serializer que grava o debug ID no bundle. Sem isso,
// o plugin nativo (`app.config.ts`) sobe o app, mas o evento do Hermes em
// produção não casa com o source map que o CI/build sobe — o Sentry mostra o
// erro sem dizer onde.
const { getSentryExpoConfig } = require("@sentry/react-native/metro");
const { withNativeWind } = require("nativewind/metro");

module.exports = withNativeWind(getSentryExpoConfig(__dirname), { input: "./global.css" });
