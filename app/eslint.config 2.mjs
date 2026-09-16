import js from "@eslint/js";
import tseslint from "typescript-eslint";

/**
 * Lint do app, SEPARADO do da raiz.
 *
 * O `eslint.config.mjs` da raiz mira `frontend/**\/*.js` e `**\/*.mjs` — regras
 * do site clássico, que não valem aqui. A raiz ignora `app/**` (ver o `ignores`
 * de lá), e este arquivo cuida deste diretório. Dois mundos, dois configs.
 */
export default tseslint.config(
  { ignores: ["node_modules/", ".expo/", "dist/", "expo-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      // Teto de tamanho, o par do gate de 350 linhas do Python
      // (tests/test_max_lines_python.py). O site já tem um arquivo de 11 mil
      // linhas; a hora de não deixar acontecer de novo é agora, com zero.
      "max-lines": ["error", { max: 350, skipBlankLines: true, skipComments: true }],
      // Valor financeiro e token nunca em console.
      "no-console": ["error", { allow: ["warn", "error"] }],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Config de ferramenta roda no Node (CommonJS), não no aparelho.
    files: ["jest.config.js", "jest.setup.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { module: "readonly", require: "readonly", jest: "readonly" },
    },
  },
  {
    files: ["__tests__/**/*.ts?(x)", "jest.setup.js"],
    languageOptions: { globals: { jest: "readonly" } },
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
);
