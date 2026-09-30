/**
 * A tampa nativa (`modules/tampa/ios`) não consegue importar os tokens nem o
 * asset do JS: são cópias. Este teste é o que as mantém iguais (CLAUDE.md §0.7)
 * — e, de quebra, falha se o `.gitignore` voltar a engolir `modules/tampa/ios/`
 * (o arquivo some do checkout do CI).
 */
import { claro, escuro } from "@/ui/tokens";

// Sem @types/node neste projeto: mesmo padrão de `ativar_mfa.test.tsx`.
declare const __dirname: string;
/* eslint-disable @typescript-eslint/no-require-imports */
const fs = require("node:fs") as { readFileSync(caminho: string, cod?: string): string & Uint8Array };
const path = require("node:path") as { resolve(...partes: string[]): string };
const cripto = require("node:crypto") as {
  createHash(alg: string): { update(d: Uint8Array): { digest(f: string): string } };
};
/* eslint-enable @typescript-eslint/no-require-imports */

const RAIZ = path.resolve(__dirname, "..", "..");
const sha = (caminho: string) => cripto.createHash("sha256").update(fs.readFileSync(path.resolve(RAIZ, caminho))).digest("hex");
const swift = () => fs.readFileSync(path.resolve(RAIZ, "modules/tampa/ios/TampaModule.swift"), "utf8");
const corDoSwift = (nome: string) => {
  const achado = swift().match(new RegExp(`let ${nome} = 0x([0-9A-Fa-f]{6})`));
  if (!achado) throw new Error(`${nome} não encontrado no TampaModule.swift`);
  return `#${achado[1]!.toUpperCase()}`;
};

describe("tampa nativa × JS", () => {
  it("o símbolo da tampa é byte a byte o da Boas-vindas", () => {
    expect(sha("modules/tampa/ios/tampa_simbolo.png")).toBe(sha("assets/brand/simbolo.png"));
  });

  it.each([
    ["fundoClaro", claro.bg],
    ["fundoEscuro", escuro.bg],
  ])("%s do Swift = bg do tema", (nome, bg) => {
    expect(corDoSwift(nome)).toBe(bg.toUpperCase());
  });
});
