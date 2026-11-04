import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ErroApi } from "../lib/v2";
import { route } from "../router";
import { Confirma } from "../conta/Confirma";
import { criarFamilia, familiaQuery, sairDaFamilia } from "./api";
import { FalhaFamilia, focarTitulo, useDonoDaTela, useEscrita } from "./escrita";
import { Historico } from "./Historico";
import { Permissoes } from "./Permissoes";
import { Titular } from "./Titular";

// Família da Conta (/conta/familia): sem família (criar), membro (sair) ou titular (Titular.tsx). A API é a api/v2/familia.py.
// De quem é a tela e o `key` por uid: `useDonoDaTela` (escrita.tsx).
export function ContaFamilia() {
  const { uid, perfilFalhou, chave } = useDonoDaTela();
  return <Tela key={chave} uid={uid} perfilFalhou={perfilFalhou} />;
}

function Tela({ uid, perfilFalhou }: { uid: number | undefined; perfilFalhou: (() => void) | undefined }) {
  const qc = useQueryClient();
  const q = useQuery(familiaQuery);
  const escrita = useEscrita();
  const { ocupado, falha, rodar } = escrita;
  const [saindo, setSaindo] = useState(false);

  const sessao = q.error instanceof ErroApi && q.error.status === 401;
  const d = q.isError ? undefined : q.data; // erro de carga nunca mostra o dado velho; `null` = sem família
  const criar = () => rodar(async () => { await criarFamilia(); await qc.invalidateQueries({ queryKey: familiaQuery.queryKey }); });
  const sair = async () => {
    // Sucesso: a página recarrega em /painel (o plano do membro muda, o /me precisa ser lido de novo). `rodar` já guarda a falha.
    if (await rodar(sairDaFamilia)) location.assign("/painel");
  };

  const visao = q.isPending ? "carga" : d === undefined ? "erro" : d === null ? "sem" : d.papel;
  useEffect(focarTitulo, [visao]); // criar, desfazer: o botão que foi clicado sai da tela

  let corpo;
  if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  else if (d === undefined) corpo = (
    <div className="empty" role="alert">
      <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar a família."}</p>
      {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>}
    </div>
  );
  else if (d === null) corpo = <>
    <button type="button" className="btn btn-primary" aria-disabled={ocupado || undefined} onClick={criar}>Criar família</button>
  </>;
  else if (d.papel === "membro") corpo = <>
    <p>Você está numa família.</p>
    <Permissoes permissoes={d.permissoes} escrita={escrita} />
    <Historico uid={uid} perfilFalhou={perfilFalhou} />
    <button type="button" className="btn btn-ghost" aria-disabled={ocupado || undefined} onClick={() => setSaindo(true)}>Sair da família</button>
    {saindo && <Confirma titulo="Sair da família?" texto="Você perde o acesso ao plano da família." botao="Sair" pendente="Saindo…" fechar={() => setSaindo(false)} agir={sair} />}
  </>;
  else corpo = <Titular v={d} escrita={escrita} uid={uid} perfilFalhou={perfilFalhou} />;

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/familia").title}</h1>
      </header>
      <div className="page-grid">
        <section className="panel span-12 conta-perfil conta-familia" aria-label="Sua família">{corpo}<FalhaFamilia falha={falha} /></section>
      </div>
    </>
  );
}
