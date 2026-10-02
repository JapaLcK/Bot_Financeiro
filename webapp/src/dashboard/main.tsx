import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { Entrada } from "./parts/Entrada";
import "./styles/index.css";

declare global { interface Window { PIGBANK_DEMO_PLAN?: unknown } }

const qc = new QueryClient();
// O protótipo (dashboard-v2/index.html) define o plano da demonstração e não fala com a
// API. O bundle nunca lê a URL: quem decide o plano é o /api/v2/me.
if (typeof window.PIGBANK_DEMO_PLAN === "string") {
  qc.setQueryData(["me"], { plan_tier: window.PIGBANK_DEMO_PLAN });
  qc.setQueryData(["assinaturas"], { servicos: [], outras: [], total_mensal: 0, total_anual: 0 });
}

const root = document.getElementById("pigbank-dashboard");
if (root) {
  createRoot(root).render(
    <StrictMode><QueryClientProvider client={qc}><Entrada><App /></Entrada></QueryClientProvider></StrictMode>,
  );
}
