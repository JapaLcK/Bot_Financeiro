/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { IconeCategoria, NOMES_ICONES_CATEGORIA } from "@/ui/componentes/IconeCategoria";

import { renderNosDoisTemas } from "./_render";

// O registro do app tem os mesmos nomes de `db/cat_icones.py` (o servidor manda `icone`; o app não importa Python, então este teste os compara, §0.7).
const py = readFileSync(resolve(__dirname, "../../../db/cat_icones.py"), "utf8");
/** Os valores únicos de `EMOJI_PARA_ICONE`. Falha ALTO se o formato mudar: lista vazia compararia verde com nada. */
function nomesDoServidor(): string[] {
  const bloco = /EMOJI_PARA_ICONE = \{([^}]*)\}/.exec(py)?.[1];
  if (!bloco) throw new Error("db/cat_icones.py: EMOJI_PARA_ICONE sumiu ou mudou de forma");
  const nomes = [...bloco.matchAll(/"[^"]+": "([a-z0-9-]+)"/g)].map((m) => m[1]!);
  const neutro = /ICONE_NEUTRO = "([a-z0-9-]+)"/.exec(py)?.[1];
  if (nomes.length < 50 || neutro !== "tag") throw new Error(`db/cat_icones.py: li ${nomes.length} entradas e neutro ${neutro}`);
  return [...new Set(nomes)];
}

describe("IconeCategoria", () => {
  it("o registro tem TODOS os nomes do servidor (e nenhum a mais)", () => {
    expect([...NOMES_ICONES_CATEGORIA].sort()).toEqual(nomesDoServidor().sort());
  });

  it("desenha o ícone pedido, decorativo para o leitor de tela; nome desconhecido ou vazio cai em `tag`", () => {
    const { claro: c } = renderNosDoisTemas(<><IconeCategoria icone="hamburger" /><IconeCategoria icone="nao-existe" /><IconeCategoria icone="" /><IconeCategoria icone="constructor" /></>);
    const ids = c.getAllByTestId(/^icone-categoria-/, { includeHiddenElements: true }).map((n) => n.props.testID);
    expect(ids).toEqual(["icone-categoria-hamburger", "icone-categoria-tag", "icone-categoria-tag", "icone-categoria-tag"]);
    expect(c.getByTestId("icone-categoria-hamburger", { includeHiddenElements: true }).props.accessibilityElementsHidden).toBe(true);
    expect(c.queryByTestId("icone-categoria-hamburger")).toBeNull(); // escondido: a consulta padrão do leitor de tela não o vê
  });
});
