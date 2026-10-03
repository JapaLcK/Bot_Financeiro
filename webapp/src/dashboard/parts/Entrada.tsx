import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ICON } from "../lib/brand";
import { useEventosV2 } from "../lib/eventos";
import { ErroApi, meQuery } from "../lib/v2";

// Conta paga sem senha (nem Google/Apple): toda a /api/v2 dá 403 `password_required`. A saída é
// a /home, onde o overlay "Crie sua senha" (frontend/criar-senha.js) sobe sozinho; o /painel
// não carrega o criar-senha.js. Usado aqui e no aviso do perfil (Board).
export const CRIAR_SENHA = { href: "/home", texto: "Criar senha" };

// Os avisos ao vivo só com o /me de pé: no erro, o portão desmonta e o stream fecha.
function Eventos() {
  useEventosV2();
  return null;
}

// Portão do painel: nada do painel monta antes do /api/v2/me (para não piscar o plano
// errado). Erro é uma tela só; a exceção é o `password_required`, que ganha o "Criar senha".
// Recarregar passa de novo pelo portão do servidor (serve_painel), que já manda cada caso ao
// lugar certo: login, /app, /precos. Por isso não há redirecionamento aqui. Limite: conta
// agendada para exclusão leva 403 e o serve_painel não barra exclusão, então Recarregar volta
// a esta tela.
export function Entrada({ children }: { children: ReactNode }) {
  const { status, error } = useQuery(meQuery);
  if (status === "success") return <><Eventos />{children}</>;
  if (status === "pending") {
    return (
      <div className="entrada" role="status">
        <img src={ICON} alt="" width={48} height={48} />
        <p>Carregando o painel…</p>
      </div>
    );
  }
  const semSenha = error instanceof ErroApi && error.code === "password_required";
  return (
    <main className="entrada">
      <img src={ICON} alt="" width={48} height={48} />
      <div role="alert">
        <h1>Não deu para carregar o painel</h1>
        <p>{semSenha ? "Para abrir o painel novo, crie a sua senha. Depois de criar, volte para o painel novo." : "Recarregue a página ou volte ao painel antigo."}</p>
      </div>
      <div className="entrada-acoes">
        {semSenha && <a className="btn btn-primary retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
        <button type="button" className={`btn ${semSenha ? "btn-ghost" : "btn-primary"}`} onClick={() => location.reload()}>Recarregar</button>
        <a className="btn btn-ghost" href="/app">Painel antigo</a>
      </div>
    </main>
  );
}
