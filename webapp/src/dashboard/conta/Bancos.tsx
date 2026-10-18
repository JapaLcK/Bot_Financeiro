import { useQuery } from "@tanstack/react-query";
import { ErroApi, bancosQuery, contaQuery } from "../lib/v2";
import { moneyText } from "../lib/format.js";
import { bancosValidos, barraDosBancos } from "../lib/bancos";
import { ICON } from "../lib/brand";
import { OF } from "../parts/guia-falas";
import { Selos } from "../parts/Selos";
import { route } from "../router";
import { conexoesQuery, limiteQuery, uidValido } from "./api";
import { BancoCard } from "./BancoCard";

// Bancos conectados (/conta/bancos), só leitura: o estado de cada conexão vem do snapshot do Open Finance (`/open-finance/{uid}`, o uid
// só da sessão), os valores de `/api/v2/bancos` e as vagas de `/limite`. Só o snapshot é obrigatório: sem `/bancos` os cards ficam sem números.
// O topo é o `total` do servidor (o cliente não soma para exibir); a barra só compara e some se algo deixar o número incerto.
export function ContaBancos() {
  // Foco relê a conta (como o Perfil): trocar de conta em outra aba muda o uid, e o do cache velho pediria o /open-finance de outra pessoa.
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true });
  const uid = conta.data?.user_id;
  const conexoes = useQuery(conexoesQuery(uid));
  const limite = useQuery(limiteQuery(uid));
  const bancos = useQuery(bancosQuery);

  const lista = conexoes.data;
  const erro = [conta.error, conexoes.error].find((e) => e) ?? null;
  const semUid = conta.isSuccess && !uidValido(uid);
  const sessao = erro instanceof ErroApi && erro.status === 401; // sessão acabada esconde o que ficou em cache
  const b = bancos.isError || !bancosValidos(bancos.data) ? undefined : bancos.data; // releitura que falhou ou fora do contrato: sem números, nunca valor velho
  const lim = limite.isError ? undefined : limite.data;
  const porId = new Map((b?.bancos ?? []).map((x) => [x.conexao_id, x]));
  const barra = b && lista ? barraDosBancos(b, lista) : [];
  const topo = b ? Number(b.total) : NaN;
  const vagas = lim && typeof lim.em_uso === "number" && typeof lim.of_banks_max === "number" && lim.of_banks_max > 0 ? `${lim.em_uso} de ${lim.of_banks_max} bancos` : null;
  // Sem vaga: a mensagem do servidor no lugar do link; sem mensagem, nenhum link (não convida a uma ação que o servidor recusa).
  const conectar = lim?.pode_adicionar !== false ? <a className="btn btn-primary btn-conectar" href={OF}>Conectar banco</a>
    : typeof lim.message === "string" && lim.message.trim() ? <p className="faint">{lim.message}</p> : null;

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/conta/bancos").title}</h1>
      </header>
      <div className="page-grid">
        {!lista && !semUid && !erro ? <p role="status" className="faint span-12">Carregando…</p>
          : !lista || sessao ? (
            <section className="panel span-12 empty" role="alert">
              <p>{sessao ? "Sua sessão terminou." : "Não deu para carregar seus bancos."}</p>
              {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
                : <button type="button" className="btn btn-ghost retry" onClick={() => { conta.refetch(); if (uidValido(uid)) conexoes.refetch(); }}>Tentar de novo</button>}
            </section>
          ) : (
            <>
              {b && lista.length > 0 && (
                <section className="panel span-12 bancos-topo" aria-label="Total nos bancos">
                  <p className="faint">Em conta e investido</p>
                  <b className="num bancos-total">{moneyText(b.total) ?? "—"}</b>
                  <Selos motivos={b.motivos} />
                  {barra.length > 0 && (
                    <div className="comp-bar" role="img" aria-label={barra.map((f) => `${f.nome} ${Math.round((Number(f.total) / topo) * 100)}%`).join(", ")}>
                      {barra.map((f) => <span key={f.conexao_id} style={{ flexGrow: Number(f.total) / topo, background: f.cor }} />)}
                    </div>
                  )}
                </section>
              )}
              {vagas && <p className="faint span-12">{vagas}</p>}
              {lista.length === 0 ? (
                <section className="panel span-12 empty">
                  <img src={ICON} alt="" width={48} height={48} />
                  <p>Nenhum banco conectado.</p>
                  {conectar}
                </section>
              ) : (
                <>
                  <ul className="span-12 bancos-lista" aria-label="Bancos">
                    {lista.map((c, i) => <BancoCard key={c.id} c={c} i={i} v={bancos.isPending ? "…" : porId.get(c.id) ?? "—"} />)}
                  </ul>
                  {conectar && <div className="span-12 bancos-conectar">{conectar}</div>}
                </>
              )}
            </>
          )}
      </div>
    </>
  );
}
