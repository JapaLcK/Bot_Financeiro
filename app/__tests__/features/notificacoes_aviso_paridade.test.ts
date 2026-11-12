/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { DESLIGADOS, DESLIGADOS_D5, DESLIGAR_AGORA, LIGUE_INSIGHTS, PLUS_E_PRO, TITULO_AVISO } from "@/features/conta/notificacoes";

// A9 do PL-06 B6: os rótulos curtos dos Avisos do Piggy são cópias do web (§0.7), e o app não importa o web. Cada âncora CAPTURA
// o literal no seu papel (o selo de "sem plano" no ramo `!noPlano`, não "algum Plus e Pro do bloco"): "Plus e Pro" também é o selo
// do Resumo semanal, e o título também está no bloco do 402. As âncoras toleram espaço, quebra de linha e atributo novo; casar zero
// ou duas vezes também é vermelho, com o nome da âncora.
const raiz = resolve(__dirname, "../../..");
const FONTES = {
  web: readFileSync(resolve(raiz, "webapp/src/dashboard/conta/Notificacoes.tsx"), "utf8"),
  html: readFileSync(resolve(raiz, "frontend/settings.html"), "utf8"),
};
type Fontes = typeof FONTES;
const APP = { titulo: TITULO_AVISO, plus: PLUS_E_PRO, ligue: LIGUE_INSIGHTS, d5: DESLIGADOS_D5, desligados: DESLIGADOS, agora: DESLIGAR_AGORA };

const TITULO_DA_LINHA = (id: string) => new RegExp(`id="${id}"[^>]*>[\\s\\S]*?class="notification-title"[^>]*>([^<]*)<`);
const ANCORAS: [nome: string, fonte: keyof Fontes, re: RegExp, copia: keyof typeof APP][] = [
  ["título no /painel", "web", /interruptor\(\s*"insight_alert_email_enabled"\s*,\s*"([^"]*)"/, "titulo"],
  ["selo sem plano", "web", /!noPlano\s*\?\s*\(\s*aderiu\s*\?\s*undefined\s*:\s*"([^"]*)"\s*\)/, "plus"],
  ["selo de Insights", "web", /!d\.insight_email_enabled\s*\?\s*"([^"]*)"/, "ligue"],
  ["D5 no /painel", "web", /!\(\s*"insight_alert_email_enabled"\s+in\s+corpo\s*\)\s*\)\s*setDesligou\(\s*"([^"]*)"/, "d5"],
  ["título no /settings", "html", TITULO_DA_LINHA("notif-insight-aviso-row"), "titulo"],
  ["título do bloco sem plano", "html", TITULO_DA_LINHA("notif-aviso-sem-plano"), "titulo"],
  ["botão", "html", /id="notif-aviso-desligar"[^>]*>([^<]*)<\/button>/, "agora"],
  ["resultado do botão", "html", /async function desligarAvisoSemPlano\(\)[\s\S]*?resultado\.textContent\s*=\s*"([^"]*)"/, "desligados"],
  ["D5 no /settings", "html", /!\(\s*"insight_alert_email_enabled"\s+in\s+patch\s*\)\s*\?\s*"([^"]*)"/, "d5"],
];

/** O literal capturado e onde ele está na fonte. Âncora que casa 0 ou 2+ vezes lança com o nome dela. */
const achar = (nome: string, texto: string, re: RegExp) => {
  const achados = [...texto.matchAll(new RegExp(re.source, "gd"))];
  if (achados.length !== 1) throw new Error(`âncora "${nome}" casou ${achados.length} vezes: o código em volta do rótulo mudou; reancore aqui`);
  const [inicio, fim] = (achados[0] as RegExpExecArray & { indices: [number, number][] }).indices[1]!;
  return { literal: texto.slice(inicio, fim).replace(/\s+/g, " ").trim(), inicio, fim };
};
const divergencias = (app = APP, fontes: Fontes = FONTES) =>
  ANCORAS.filter(([nome, f, re, copia]) => achar(nome, fontes[f], re).literal !== app[copia]).map(([nome]) => nome);

it("A9: os rótulos curtos do app são os mesmos do /painel e do /settings, cada um no seu papel", () => {
  expect(divergencias()).toEqual([]);
});

it("A9: controle negativo — o comparador acusa a cópia do app que mudou", () => {
  expect(divergencias({ ...APP, agora: "Desligar" })).toEqual(["botão"]);
  expect(divergencias({ ...APP, d5: "Avisos do Piggy desligados." })).toEqual(["D5 no /painel", "D5 no /settings"]);
});

it("A9: controle negativo — trocar na fonte o literal de UMA âncora acusa só ela (o trecho sai da própria fonte)", () => {
  for (const [nome, f, re] of ANCORAS) {
    const { inicio, fim } = achar(nome, FONTES[f], re);
    const mutada = { ...FONTES, [f]: `${FONTES[f].slice(0, inicio)}Outro rótulo${FONTES[f].slice(fim)}` };
    expect({ trocado: nome, acusou: divergencias(APP, mutada) }).toEqual({ trocado: nome, acusou: [nome] });
  }
});
