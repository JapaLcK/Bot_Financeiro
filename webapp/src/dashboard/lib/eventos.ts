// Avisos ao vivo da /api/v2/eventos (SSE). Qualquer aviso (e cada conexão aberta, que
// pode ter perdido avisos enquanto caída) invalida TODAS as consultas: o `recurso` do
// aviso é ignorado de propósito. Só monta depois do portão (parts/Entrada.tsx).
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { meQuery } from "./v2";

export function useEventosV2() {
  const qc = useQueryClient();
  useEffect(() => {
    // O protótipo (dashboard-v2/index.html) não tem backend.
    if (typeof window.PIGBANK_DEMO_PLAN === "string") return;
    let fonte: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let espera = 1000;
    let vivo = true;
    const abrir = () => {
      const es = new EventSource("/api/v2/eventos");
      fonte = es;
      es.onopen = () => { espera = 1000; qc.invalidateQueries(); };
      es.onmessage = () => { qc.invalidateQueries(); };
      es.onerror = async () => {
        // CONNECTING: queda de rede ou fim do stream, o navegador reconecta sozinho.
        // CLOSED: o servidor recusou (401, 429…) e o EventSource desistiu.
        if (es.readyState !== EventSource.CLOSED) return;
        // O /me passa pelo auth-refresh.js, que renova a sessão no 401. Deu certo:
        // reabre com espera crescente. Falhou: o portão mostra o erro e desmonta isto
        // (a limpeza cancela a reabertura).
        await qc.refetchQueries({ queryKey: meQuery.queryKey });
        if (!vivo) return;
        timer = setTimeout(abrir, espera);
        espera = Math.min(espera * 2, 60000);
      };
    };
    abrir();
    return () => { vivo = false; fonte?.close(); clearTimeout(timer); };
  }, [qc]);
}
