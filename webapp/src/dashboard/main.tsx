import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { Entrada } from "./parts/Entrada";
import { Cofre } from "./conta/Cofre";
import { BALANCE_TODAY, MONTHS, previousKey, summary } from "./lib/api";
import { readProfile } from "./lib/profiles.js";
import { DEMO, contasQuery, escutarLogout, investidoQuery, orcamentoQuery, orcamentosCategoriaQuery, perfilQuery, resumoMesQuery } from "./lib/v2";
import type { Assinaturas, Investido, OrcamentoDomestico, OrcamentosCategoria } from "./lib/api-v2.gen";
import "./styles/index.css";

declare global { interface Window { PIGBANK_DEMO_PLAN?: unknown } }

const qc = new QueryClient();
// O protótipo (dashboard-v2/index.html) define o plano da demonstração e não fala com a
// API: o cache nasce com os dados sintéticos e o perfil salvo no navegador. O bundle nunca
// lê a URL: quem decide o plano é o /api/v2/me.
if (DEMO) {
  const txt = (n: number) => n.toFixed(2);
  const conta = (id: number, instituicao: string, saldo: number) => ({
    id, instituicao, nome: "Conta", saldo: txt(saldo), moeda: "BRL", no_total: true, conexao: "updated", sincronizado_em: null, motivos: [],
  });
  qc.setQueryData(["me"], { plan_tier: window.PIGBANK_DEMO_PLAN });
  qc.setQueryData<Assinaturas>(["assinaturas"], { servicos: [], outras: [], ignoradas: [], total_mensal: "0", total_anual: "0" });
  qc.setQueryData(perfilQuery.queryKey, { perfil: readProfile() });
  qc.setQueryData(contasQuery.queryKey, {
    total: txt(BALANCE_TODAY + 60), motivos: ["carteira_nao_confirmada"], fora_do_total: 0,
    carteira: { saldo: txt(60), motivos: ["carteira_nao_confirmada"] },
    contas: [conta(1, "Nubank", BALANCE_TODAY - 250), conta(2, "Inter", 250)],
  });
  // Os rótulos são cópia de `db/investido.py::TIPOS` (dado de exemplo: deriva sem dano).
  qc.setQueryData<Investido>(investidoQuery.queryKey, {
    total: "23480.00", motivos: [],
    por_tipo: [{ tipo: "renda_fixa", rotulo: "Renda fixa", valor: "20000.00", motivos: [] },
      { tipo: "acoes", rotulo: "Ações", valor: "3480.00", motivos: [] }],
    por_banco: [{ banco: "Nubank", valor: "23480.00", motivos: [] }],
  });
  // Orçamentos por categoria de exemplo (só o mês corrente, como o real): um por status.
  const hoje = new Date();
  const ORC = [["alimentação", "alimentacao", "fork-knife", "🍔", "#f59e0b", 800, 412.3], ["transporte", "transporte", "car", "🚗", "#38bdf8", 300, 255],
    ["lazer", "lazer", "confetti", "🎉", "#f0abfc", 200, 236.5], ["saúde", "saude", "heartbeat", "💊", "#6ee7b7", 150, 0]] as const;
  const orcs = ORC.map(([categoria, chave, icone, emoji, cor, teto, gasto]) => {
    const uso = gasto / teto * 100;
    return { categoria, chave, emoji, icone, cor, teto: txt(teto), gasto: txt(gasto), restante: txt(teto - gasto), usado_pct: txt(uso),
      status: uso >= 100 ? "vermelho" as const : uso >= 80 ? "amarelo" as const : "verde" as const };
  });
  const somaO = (k: "teto" | "gasto") => orcs.reduce((a, o) => a + Number(o[k]), 0);
  qc.setQueryData<OrcamentosCategoria>(orcamentosCategoriaQuery.queryKey, {
    mes: `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, "0")}`, limite: null, orcamentos: orcs,
    total: { teto: txt(somaO("teto")), gasto: txt(somaO("gasto")), restante: txt(somaO("teto") - somaO("gasto")), usado_pct: txt(somaO("gasto") / somaO("teto") * 100), em_risco: 2 },
  });
  for (const mes of MONTHS) {
    const m = summary(mes), prev = previousKey(mes);
    const anterior = prev && { mes: prev, entrou: txt(summary(prev).income), saiu: txt(summary(prev).expense) };
    const ate = `${mes}-${new Date(Number(mes.slice(0, 4)), Number(mes.slice(5)), 0).getDate()}`;
    qc.setQueryData(resumoMesQuery(mes).queryKey, { mes, ate, entrou: txt(m.income), saiu: txt(m.expense), anterior, motivos: [] });
    // Potes de exemplo: teto = % da renda do mês; o gasto é uma fração fixa do teto, só para a tela ter o que mostrar.
    const POTES = [["custos_fixos", "Custos fixos", "#7dd3fc", 55, 0.9], ["conforto", "Conforto", "#6ee7b7", 5, 0.5], ["metas", "Metas", "#fde047", 10, 0.6],
      ["prazeres", "Prazeres", "#f0abfc", 10, 1.2], ["liberdade_financeira", "Liberdade financeira", "#93c5fd", 10, 0.8], ["conhecimento", "Conhecimento", "#fdba74", 10, 0.3]] as const;
    const potes = POTES.map(([chave, nome, cor, pct, uso]) => {
      const teto = Math.round(m.income * pct) / 100, gasto = Math.round(teto * uso * 100) / 100;
      return { chave, nome, cor, pct: txt(pct), teto: txt(teto), gasto: txt(gasto), restante: txt(teto - gasto), guardado: chave === "metas" || chave === "liberdade_financeira", usado_pct: teto > 0 ? txt(uso * 100) : null, categorias: [], por_categoria: [], lancamentos: [] }; // sem lançamentos no protótipo
    });
    const soma = (k: "teto" | "gasto") => potes.reduce((a, p) => a + Number(p[k]), 0);
    qc.setQueryData<OrcamentoDomestico>(orcamentoQuery(mes).queryKey, {
      mes, renda: { valor: txt(m.income), origem: "calculada" }, potes,
      total: { teto: txt(soma("teto")), gasto: txt(soma("gasto")), restante: txt(soma("teto") - soma("gasto")), restante_livre: txt(potes.reduce((a, p) => a + (p.guardado ? Math.max(Number(p.restante), 0) : Number(p.restante)), 0)), usado_pct: soma("teto") > 0 ? txt(soma("gasto") / soma("teto") * 100) : null },
    });
  }
}

const root = document.getElementById("pigbank-dashboard");
if (root) {
  if (!DEMO) escutarLogout(); // sair em outra aba derruba o painel (o dashboard.js antigo faz o mesmo)
  createRoot(root).render(
    <StrictMode><QueryClientProvider client={qc}><Entrada><App /></Entrada>{/* irmão do portão: o /me falhar não apaga os códigos de backup do MFA */}<Cofre /></QueryClientProvider></StrictMode>,
  );
}
