import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ICON } from "../lib/brand";
import { useEventosV2 } from "../lib/eventos";
import { meQuery } from "../lib/v2";

// Os avisos ao vivo só com o /me de pé: no erro, o portão desmonta e o stream fecha.
function Eventos() {
  useEventosV2();
  return null;
}

// Portão do painel: nada do painel monta antes do /api/v2/me (para não piscar o plano
// errado). Erro de qualquer tipo é uma tela só. Recarregar passa de novo pelo portão
// do servidor (serve_painel), que já manda cada caso ao lugar certo: login, /app,
// /precos. Por isso não há redirecionamento aqui. Limite: conta agendada para exclusão
// leva 403 e o serve_painel não barra exclusão, então Recarregar volta a esta tela.
export function Entrada({ children }: { children: ReactNode }) {
  const { status } = useQuery(meQuery);
  if (status === "success") return <><Eventos />{children}</>;
  if (status === "pending") {
    return (
      <div className="entrada" role="status">
        <img src={ICON} alt="" width={48} height={48} />
        <p>Carregando o painel…</p>
      </div>
    );
  }
  return (
    <main className="entrada">
      <img src={ICON} alt="" width={48} height={48} />
      <div role="alert">
        <h1>Não deu para carregar o painel</h1>
        <p>Recarregue a página ou volte ao painel antigo.</p>
      </div>
      <div className="entrada-acoes">
        <button type="button" className="btn btn-primary" onClick={() => location.reload()}>Recarregar</button>
        <a className="btn btn-ghost" href="/app">Painel antigo</a>
      </div>
    </main>
  );
}
