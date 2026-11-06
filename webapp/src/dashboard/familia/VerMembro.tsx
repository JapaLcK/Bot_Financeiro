import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Linha } from "../widgets/OrcamentosCategoria";
import { moneyText, monthTitle } from "../lib/format.js";
import { MESES, mesAtual } from "../lib/v2";
import { familiaQuery, falhaFamilia, primeiroNome, type Membro } from "./api";
import { agregadosQuery, doMembro, motivoDe } from "./dados";
import { FalhaFamilia, PerfilFalhou } from "./escrita";
import { MembroLancamentos } from "./MembroLancamentos";

// Titular: o resumo do mês (e os lançamentos, se liberados) de UM membro. Tudo em memória, `gcTime: 0`: sair daqui apaga o dado.
// Cada leitura grava uma linha no histórico do membro. REGRA (dono, 2026-11-03): com o painel aberto, mudar a lista de permissões/status NUNCA lê; MENOS acesso vira texto na hora,
// MAIS acesso só vale depois de "Voltar" + "Ver" (remonta). Por isso o que o painel PODE mostrar é congelado na abertura (`viu`) e depois só entram reduções (refs pegajosos).
//   evento (painel aberto)                         | resumo            | resumo + lançamentos            | parou (total)  | parou só lançamentos
//   lista: perde agregados / status != ativo       | parou, sem GET    | parou, sem GET                  | segue          | segue
//   lista: perde lancamentos                       | segue (sem seção) | texto "…os lançamentos", sem GET | segue          | segue
//   lista: GANHA lancamentos (1ª vez ou religar)   | segue, SEM seção  | segue                           | segue          | segue (texto)
//   lista: religa agregados / reativa (ativo)      | segue             | segue                           | segue (texto)  | segue
//   403 consent_required no resumo                 | parou             | parou                           | segue          | parou
//   403 consent_required nos lançamentos           | n/a               | texto dos lançamentos           | n/a            | segue
//   404 (resumo ou lançamentos)                    | "não está mais na família"; a lista é relida (no resumo e nos lançamentos), o "Ver" some e o painel fecha
//   402 (plano)                                    | "Seu plano não inclui família." + link da cobrança; não relê
//   401 (sessão)                                   | "Sua sessão terminou." + Recarregar; não relê
//   5xx / rede                                     | 3 GETs, depois "Não foi possível carregar." + "Tentar de novo" (relê só o que falhou)
//   422 mes (resumo)                               | "Sem dados antes de {Nome} entrar."; trava o "Mês anterior"
//   422 cursor (lançamentos, "Ver mais")           | descarta as páginas e relê a 1ª UMA vez (+1 linha no "Quem viu"); uma 2ª vez vira erro
//   troca de mês (300 ms)                          | lê o mês novo, só o que `viu` mostra
//   Voltar + Ver                                   | remonta: lê com a lista atual
// Só o retry de rede/5xx relê (até 3 GETs por leitura). O erro nunca mostra o dado velho (`q.isError ? undefined : q.data`):
// defesa sem teste que a isole (com `gcTime: 0` e a chave por mês o dado velho já não existe; o mesmo vale para o `removeQueries` de `parou` e o `key={mes}` do Resumo). Não tire sem pensar.
export function VerMembro({ uid, perfilFalhou, membro, voltar }: { uid: number | undefined; perfilFalhou: (() => void) | undefined; membro: Membro; voltar: () => void }) {
  const qc = useQueryClient();
  const nome = primeiroNome(membro.nome);
  const [mes, setMes] = useState(mesAtual()); // o mês no rótulo
  const [lido, setLido] = useState(mes); // o mês que se lê: segue `mes` 300 ms depois do último clique (cada GET grava uma linha no histórico do membro)
  const titulo = useRef<HTMLHeadingElement>(null);
  const [limite, setLimite] = useState<string | null>(null); // o mês em que o servidor disse "antes de entrar": dali para trás não há o que ver
  const [parouAqui, setParouAqui] = useState(false); // o 403 de um resumo
  const viu = useRef(membro.permissoes.includes("lancamentos")); // congelado na abertura: ganhar `lancamentos` depois não monta a seção (monta = GET)
  const parouLista = useRef(false); // PEGAJOSO: visto uma vez, fica até o painel fechar
  if (!membro.permissoes.includes("agregados") || membro.status !== "ativo") parouLista.current = true;
  const parou = parouAqui || parouLista.current;
  const semLanc = useRef(false);
  if (viu.current && !membro.permissoes.includes("lancamentos")) semLanc.current = true;
  const reler = () => void qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
  const i = MESES.indexOf(mes);

  useEffect(() => titulo.current?.focus(), []); // o "Ver" que abriu o painel fica na lista hidden: o foco vai ao título
  useEffect(() => { const t = setTimeout(() => setLido(mes), 300); return () => clearTimeout(t); }, [mes]);

  // 403 nos resumos: o dado deste membro sai da memória, a família é relida (o "Ver" some) e só fica o texto.
  // Só o 403 relê a família: quando `parou` veio da lista, o aviso já a releu.
  useEffect(() => { if (parou) { qc.removeQueries(doMembro(membro.ref)); if (parouAqui) reler(); } }, [parou]);

  return <div className="fam-ver">
    <button type="button" className="btn btn-ghost" onClick={voltar}><i className="ph ph-arrow-left" aria-hidden="true" /> Voltar</button>
    <h2 className="fam-ver-nome" ref={titulo} tabIndex={-1}>{nome}</h2>
    {parou ? <p role="alert" className="warn">{nome} parou de compartilhar.</p> : <>
      <div className="month-switch" role="group" aria-label="Mês exibido">
        <button className="icon-btn" type="button" aria-label="Mês anterior" disabled={i <= 0 || (limite !== null && mes <= limite)} onClick={() => setMes(MESES[i - 1])}><i className="ph ph-arrow-left" aria-hidden="true" /></button>
        <p className="month-title" aria-live="polite">{monthTitle(mes)}</p>
        <button className="icon-btn" type="button" aria-label="Próximo mês" disabled={i < 0 || i === MESES.length - 1} onClick={() => setMes(MESES[i + 1])}><i className="ph ph-arrow-right" aria-hidden="true" /></button>
      </div>
      {perfilFalhou ? <PerfilFalhou tentar={perfilFalhou} /> : <>
        {lido !== mes && <p role="status" className="faint">Carregando…</p>}
        {/* o Resumo de `lido` fica montado (escondido) durante o atraso: desmontar com `gcTime: 0` faria a ida e volta reler o mesmo mês */}
        <div hidden={lido !== mes}>
          <Resumo key={lido} uid={uid} membro={membro} nome={nome} mes={lido} aoParar={() => setParouAqui(true)} aoSemDados={() => setLimite((l) => (l === null || lido > l ? lido : l))} reler={reler}
            lancamentos={(nomes) => (!viu.current ? null : !semLanc.current ? <MembroLancamentos uid={uid} refMembro={membro.ref} nome={nome} mes={lido} nomes={nomes} aoParar={() => { semLanc.current = true; reler(); }} aoSair={reler} />
              : <p role="alert" className="warn">{nome} parou de compartilhar os lançamentos.</p>)} />
        </div>
      </>}
    </>}
  </div>;
}

function Resumo({ uid, membro, nome, mes, aoParar, aoSemDados, reler, lancamentos }: {
  uid: number | undefined; membro: Membro; nome: string; mes: string; aoParar: () => void; aoSemDados: () => void; reler: () => void; lancamentos: (nomes: Map<string, string>) => ReactNode;
}) {
  const q = useQuery(agregadosQuery(uid, membro.ref, mes));
  const motivo = q.isError ? motivoDe(q.error) : null;
  const d = q.isError ? undefined : q.data;
  useEffect(() => {
    if (motivo === "consentimento") aoParar();
    else if (motivo === "saiu") reler();
    else if (motivo === "mes") aoSemDados();
  }, [motivo]);

  if (q.isPending) return <p role="status" className="faint">Carregando…</p>;
  if (motivo === "consentimento") return null; // o painel troca por "parou de compartilhar"
  if (motivo === "mes") return <p role="status" className="faint">Sem dados antes de {nome} entrar.</p>;
  if (motivo === "saiu") return <p role="alert" className="warn">{nome} não está mais na família.</p>;
  if (motivo === "plano") return <FalhaFamilia falha={falhaFamilia(q.error)} />;
  if (!d) return (
    <div className="empty" role="alert">
      <p>{motivo === "sessao" ? "Sua sessão terminou." : "Não foi possível carregar."}</p>
      {motivo === "sessao" ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => void q.refetch()}>Tentar de novo</button>}
    </div>
  );
  const nomes = new Map(d.categorias.map((c) => [c.chave, c.nome]));
  return <>
    <dl className="fam-valores">
      <div><dt>Entrou</dt><dd className={`num${/[1-9]/.test(d.entrou) && !d.entrou.startsWith("-") ? " gain" : ""}`}>{moneyText(d.entrou)}</dd></div>
      <div><dt>Saiu</dt><dd className="num">{moneyText(d.saiu)}</dd></div>
    </dl>
    {d.inicio_do_historico && d.historico_desde && <p className="faint">Desde {d.historico_desde.slice(8, 10)}/{d.historico_desde.slice(5, 7)}.</p>}
    {d.categorias.length > 0 && <section aria-labelledby="fam-cat-t">
      <h3 id="fam-cat-t">Categorias</h3>
      <ul className="fam-cats">{d.categorias.map((c) => <li key={c.chave}><span>{c.nome}</span><span className="num">{moneyText(c.valor)}</span></li>)}</ul>
    </section>}
    {d.orcamentos && d.orcamentos.length > 0 && <section aria-labelledby="fam-orc-t">
      <h3 id="fam-orc-t">Orçamentos</h3>
      <ul className="orc-lista">{d.orcamentos.map((o) => <li key={o.chave ?? o.categoria}><div className="orc-linha"><Linha o={o} /></div></li>)}</ul>
    </section>}
    {lancamentos(nomes)}
  </>;
}
