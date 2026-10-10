import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { Entrada } from "./parts/Entrada";
import { BALANCE_TODAY, MONTHS, previousKey, summary } from "./lib/api";
import { readProfile } from "./lib/profiles.js";
import { DEMO, contasQuery, investidoQuery, perfilQuery, resumoMesQuery } from "./lib/v2";
import type { Assinaturas, Investido } from "./lib/api-v2.gen";
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
  for (const mes of MONTHS) {
    const m = summary(mes), prev = previousKey(mes);
    const anterior = prev && { mes: prev, entrou: txt(summary(prev).income), saiu: txt(summary(prev).expense) };
    const ate = `${mes}-${new Date(Number(mes.slice(0, 4)), Number(mes.slice(5)), 0).getDate()}`;
    qc.setQueryData(resumoMesQuery(mes).queryKey, { mes, ate, entrou: txt(m.income), saiu: txt(m.expense), anterior, motivos: [] });
  }
}

const root = document.getElementById("pigbank-dashboard");
if (root) {
  createRoot(root).render(
    <StrictMode><QueryClientProvider client={qc}><Entrada><App /></Entrada></QueryClientProvider></StrictMode>,
  );
}
