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
    files: ["jest.config.js", "jest.setup.js", "metro.config.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { module: "readonly", require: "readonly", jest: "readonly", __dirname: "readonly" },
    },
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  {
    files: ["__tests__/**/*.ts?(x)", "jest.setup.js"],
    languageOptions: { globals: { jest: "readonly" } },
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
  {
    // `require()` de fonte estática é o jeito do Metro resolver o asset (ver
    // comentário em `app/_layout.tsx`); `import` dinâmico não bundla o TTF.
    files: ["app/_layout.tsx"],
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  {
    // As três áreas onde o design system é DESENHADO: os próprios componentes,
    // o catálogo que os exibe e as seções do catálogo extraídas por tamanho
    // (`src/ui/ds/`, CLAUDE.md §0.5). O gate SÓ vale aqui — uma tela de
    // produto fora destes caminhos (`app/index.tsx`, por exemplo) não herda
    // nada disto: ela pode importar `Text`/`TextInput` crus e colar hex à
    // vontade sem o lint acusar. "Herdar por só ter acesso ao Texto/Input"
    // seria convenção, não regra: nada aqui barra o import fora desses globs.
    files: ["src/ui/componentes/**/*.ts?(x)", "app/_ds/**/*.ts?(x)", "src/ui/ds/**/*.ts?(x)"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "react-native",
              importNames: ["Text", "TextInput"],
              message: "Use `Texto`/`Input` do design system — são o único lugar com o teto de fonte e o tabular-nums resolvidos.",
            },
            {
              name: "phosphor-react-native",
              message: "Importe o ícone específico (`phosphor-react-native/src/icons/<Nome>`); a raiz do pacote pesa 23 MB.",
            },
          ],
        },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/]",
          message: "Cor hex fora de `tokens.ts`. Use um token semântico (`cores.<nome>`).",
        },
        {
          // O literal acima não pega hex dentro de um template string
          // (`` `#${x}` `` ou até `` `#FF2D8E` ``): ali o valor não é um
          // `Literal`, é um `TemplateElement`. Mesmo hex, nó de AST diferente.
          selector: "TemplateElement[value.raw=/#[0-9a-fA-F]{3,8}/]",
          message: "Cor hex fora de `tokens.ts`. Use um token semântico (`cores.<nome>`).",
        },
      ],
    },
  },
  {
    // `Texto`/`Input` são o ÚNICO lugar autorizado a chamar `Text`/`TextInput`
    // crus — é o que a regra acima protege.
    files: ["src/ui/componentes/Texto.tsx", "src/ui/componentes/Input.tsx"],
    rules: { "no-restricted-imports": "off" },
  },
  {
    // `AmountInput` precisa do `TextInput` cru (não compõe sobre `Input` —
    // nasce no C1, ver plano do PR B): a regra geral bloqueia `Text` E
    // `TextInput`; aqui ela é REDECLARADA (não desligada) liberando só
    // `TextInput`, então `Text` e a raiz do phosphor continuam barrados.
    files: ["src/ui/componentes/AmountInput.tsx"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "react-native",
              importNames: ["Text"],
              message: "Use `Texto` do design system — é o único lugar com o teto de fonte e o tabular-nums resolvidos.",
            },
            {
              name: "phosphor-react-native",
              message: "Importe o ícone específico (`phosphor-react-native/src/icons/<Nome>`); a raiz do pacote pesa 23 MB.",
            },
          ],
        },
      ],
    },
  },
);
