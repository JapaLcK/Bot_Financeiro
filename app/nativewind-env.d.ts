/// <reference types="nativewind/types" />

// `import "../global.css"` (`app/_layout.tsx`) precisa de `declare module
// "*.css"` para o `tsc`. Quem declara é `expo/types/global.d.ts`, que o
// `expo-env.d.ts` referencia — mas esse arquivo é GERADO pelo `expo start`
// (não versionado; aparece como untracked, não gitignored — `git
// check-ignore` não acha regra nenhuma para ele) e não existe num checkout
// limpo nem no CI (`.github/workflows/app.yml` roda o typecheck sem nunca
// chamar `expo start`). Referenciar aqui, num arquivo versionado, evita
// depender de alguém ter rodado o Expo antes.
/// <reference types="expo/types" />
