/**
 * @jest-environment node
 */
// Ambiente `node`: só lê arquivos, e o do preset resolveria o `yaml` pela entrada ESM de navegador.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { parse } from "yaml";

// Quem escolhe a versão destes pacotes é o SDK do Expo (`expo/bundledNativeModules.json` do expo
// instalado, mais o `expo` e o par `react-test-renderer`), e o passo `Confere versões nativas` do CI
// reprova versão fora da faixa do mapa (#1442, #466). Decisão do dono: o Dependabot do `/app` não
// sobe nenhum deles, patch incluído, para que só `npx expo install --fix` os mova. O tooling que o SDK
// escolhe pela API (`TOOLING_SDK`) fica bloqueado pelo menos em major e minor: o patch cabe na faixa `~`
// de `typescript`, `@types/react` e `jest`; o `@types/jest` o SDK fixa exato e fica parado em todos os eixos.
type Ignore = { "dependency-name": string; "update-types"?: string[]; versions?: string[] };
type Bloco = { "package-ecosystem": string; directory: string; ignore?: Ignore[] };

const pkg = JSON.parse(readFileSync(resolve(__dirname, "../package.json"), "utf8")) as {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
};
const declarados = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
const mapa = JSON.parse(readFileSync(require.resolve("expo/bundledNativeModules.json"), "utf8")) as Record<string, string>;
// Fora do mapa, mas presos a quem está nele: o `react-test-renderer` tem de ter a versão exata do `react`.
const PARES: Record<string, string> = { "react-test-renderer": "react" };
const fixados = new Set([
  "expo", // a raiz do SDK não está no mapa dela mesma
  ...declarados.filter((n) => n in mapa),
  ...declarados.filter((n) => PARES[n] !== undefined && declarados.includes(PARES[n]!)),
]);
// `relatedPackages` de `api.expo.dev/v2/versions/latest`, que o `expo install` só junta ao mapa no modo
// online. Não há fonte offline para conferir esta lista: reconferir ao subir o SDK.
const TOOLING_SDK = ["typescript", "@types/react", "jest", "@types/jest"];

const blocos = (parse(readFileSync(resolve(__dirname, "../../.github/dependabot.yml"), "utf8")) as { updates: Bloco[] }).updates;
const apps = blocos.filter((b) => b["package-ecosystem"] === "npm" && b.directory === "/app");
const app = apps[0];
const ignore = app?.ignore ?? [];
const MAJOR_MINOR = ["version-update:semver-major", "version-update:semver-minor"];
const TODOS = [...MAJOR_MINOR, "version-update:semver-patch"];

/** `dependency-name` do Dependabot: `*` casa qualquer sequência, o resto é literal. */
function casa(padrao: string, nome: string): boolean {
  const re = padrao.split("*").map((p) => p.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*");
  return new RegExp(`^${re}$`).test(nome);
}
// `versions` restringe o `ignore` às versões listadas: não é bloqueio total.
const bloqueia = (i: Ignore, eixos: string[]) =>
  !i.versions && (!i["update-types"] || eixos.every((t) => i["update-types"]!.includes(t)));
const temBloqueio = (n: string, eixos: string[]) => ignore.some((i) => casa(i["dependency-name"], n) && bloqueia(i, eixos));

describe("dependabot.yml — /app não sobe o que o SDK do Expo escolhe", () => {
  it("todo pacote escolhido pelo SDK tem `ignore` (fixados: todos os eixos; tooling: pelo menos major e minor)", () => {
    const soltos = [
      ...[...fixados].filter((n) => !temBloqueio(n, TODOS)),
      ...TOOLING_SDK.filter((n) => declarados.includes(n) && !temBloqueio(n, MAJOR_MINOR)),
    ];
    expect(soltos.sort()).toEqual([]);
  });

  it("toda entrada do `ignore` casa algum declarado, e só pacote escolhido pelo SDK", () => {
    const erros = ignore.flatMap((i) => {
      const nome = i["dependency-name"];
      const casados = declarados.filter((n) => casa(nome, n));
      if (casados.length === 0) return [`${nome}: não casa nenhum pacote declarado`];
      return casados
        .filter((n) => !fixados.has(n) && !TOOLING_SDK.includes(n))
        .map((n) => `${nome}: pega ${n}, que o SDK não escolhe`);
    });
    expect(erros).toEqual([]);
  });

  it("sentinelas: o teste está olhando o bloco, o mapa e os pacotes certos", () => {
    expect(apps).toHaveLength(1); // um segundo bloco `/app` escaparia da guarda
    expect(mapa).toHaveProperty("react-native");
    for (const n of ["react-native-reanimated", "react-test-renderer", "expo-router", "jest-expo"]) expect(fixados).toContain(n);
    // Fora do SDK: o Dependabot continua subindo estes.
    for (const n of ["posthog-react-native", "zod", "typescript-eslint", "react-native-pluggy-connect"]) {
      expect(declarados).toContain(n);
      expect(fixados).not.toContain(n);
      expect(ignore.filter((i) => casa(i["dependency-name"], n))).toEqual([]);
    }
  });
});
