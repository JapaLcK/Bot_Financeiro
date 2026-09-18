/** Testes do app. `jest-expo` entende os módulos nativos e o transform do RN. */
module.exports = {
  preset: "jest-expo",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.js"],
  testMatch: ["<rootDir>/__tests__/**/*.test.ts?(x)"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    // Ícone por stub: o componente `Icone` (PR C1) importa
    // `phosphor-react-native/src/icons/<Nome>` um a um. No Jest, um SVG real
    // por ícone deixaria snapshot e teste reféns do desenho do traço; o stub
    // devolve um `View` vazio, e o comportamento (nome, tamanho) é do
    // componente, não do ícone.
    "^phosphor-react-native/src/icons/.*$": "<rootDir>/__tests__/ui/__mocks__/iconeStub.tsx",
    // `global.css` (Nativewind): quem processa `@tailwind` é o PostCSS via
    // Metro. Jest não tem esse transform, e sem o mock o `require` do CSS cru
    // quebra todo teste que monta `app/_layout.tsx` (ver `layout.test.tsx`).
    "\\.css$": "<rootDir>/jest.css-stub.js",
  },
  // Definido aqui e não deixado no default do preset porque `jest-expo` SUBSTITUI
  // (não estende) essa lista quando ela é dada no config do projeto — copiamos o
  // default dele e acrescentamos o pacote que falta: `phosphor-react-native`
  // publica os ícones como `.tsx` fonte (ver package.json do pacote, campo
  // `react-native`), que precisa passar pelo babel como qualquer JSX do app.
  transformIgnorePatterns: [
    "/node_modules/(?!(.pnpm|react-native|@react-native|@react-native-community|expo|@expo|@expo-google-fonts|react-navigation|@react-navigation|@sentry/react-native|native-base|standard-navigation|phosphor-react-native))",
    "/node_modules/react-native-reanimated/plugin/",
    "/node_modules/@react-native/babel-preset/",
  ],
};
