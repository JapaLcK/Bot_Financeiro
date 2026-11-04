import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { moneyText, monthTitle } from "../lib/format.js";
import { ehNegativo } from "../lib/metas-textos";
import { ErroApi } from "../lib/v2";
import { href, route } from "../router";
import { Confirma } from "../conta/Confirma";
import { FalhaFamilia, PerfilFalhou, focarTitulo, useDonoDaTela } from "./escrita";
import { apagar, casaQuery, deveReler, falhaCasa, type Casa, type FalhaCasa, type MetaCasa, type OrcamentoCasa, type Tipo } from "./casa";
import { AvisoCasa } from "./CasaDialogo";
import { CasaMetaForm } from "./CasaMetaForm";
import { CasaMovimento, type Movimento } from "./CasaMovimento";
import { CasaOrcamentoForm } from "./CasaOrcamentoForm";

// A casa do grupo (/casa): metas e orçamentos que a família divide; qualquer membro ativo lê e escreve. A API é a api/v2/familia_casa.py (casa.ts). Sem família: o caminho para criar uma.
// Nada é otimista: a tela só muda com a Casa que o servidor devolveu. Number() só dá a largura das barras.
export function PaginaCasa() {
  const { uid, perfilFalhou, chave } = useDonoDaTela();
  return <Tela key={chave} uid={uid} perfilFalhou={perfilFalhou} />;
}

type Aberto =
  | { k: "meta"; meta?: MetaCasa } | { k: "orc"; orc?: OrcamentoCasa }
  | { k: "mov"; m: Movimento } | { k: "apagar"; tipo: Tipo; ref: string; nome: string };

const largura = (n: number) => `${Math.min(Math.max(Number.isFinite(n) ? n : 0, 0), 100)}%`;
const semZero = (s: string) => !/[1-9]/.test(s);
const dia = (iso: string) => iso.split("-").reverse().join("/");
const quem = (a: string | null) => (a === null ? "Ex-membro" : a === "" ? "Membro" : a);

function Tela({ uid, perfilFalhou }: { uid: number | undefined; perfilFalhou: (() => void) | undefined }) {
  const qc = useQueryClient();
  const q = useQuery(casaQuery(uid));
  const [aberto, setAberto] = useState<Aberto | null>(null);
  const [falha, setFalha] = useState<FalhaCasa | null>(null); // o erro do "Apagar" (o das demais escritas mora no diálogo)
  useEffect(focarTitulo, [aberto]); // o botão que abriu o diálogo pode ter saído da tela (item apagado)
  useEffect(() => { if (aberto && !q.data) setAberto(null); }, [aberto, q.data]); // 404/sem família: `aberto` não sobrevive ao diálogo (a família voltando não o reabre com a foto velha)

  const sessao = q.error instanceof ErroApi && q.error.status === 401;
  const casa: Casa | null | undefined = q.isError ? undefined : q.data; // erro de carga nunca mostra o dado velho; `null` = sem família
  const fechar = () => setAberto(null);
  const abrir = (a: Aberto) => { setFalha(null); setAberto(a); };
  const reler = () => qc.invalidateQueries({ queryKey: casaQuery(uid).queryKey });

  let corpo;
  if (uid === undefined && perfilFalhou) corpo = <PerfilFalhou tentar={perfilFalhou} />;
  else if (q.isPending) corpo = <p role="status" className="faint">Carregando…</p>;
  else if (casa === undefined) corpo = (
    <div className="empty" role="alert">
      <p>{sessao ? "Sua sessão terminou." : "Não foi possível carregar a casa."}</p>
      {sessao ? <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
        : <button type="button" className="btn btn-ghost retry" onClick={() => q.refetch()}>Tentar de novo</button>}
    </div>
  );
  else if (casa === null) corpo = <>
    <p>A casa é da família: metas e orçamentos que todos veem e mudam.</p>
    <a className="btn btn-primary" href={href("/conta/familia")}>Criar família</a>
  </>;
  else corpo = <>
    <section className="casa-sec" aria-labelledby="casa-metas">
      <div className="casa-topo"><h2 id="casa-metas">Metas</h2><button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "meta" })}>Nova meta</button></div>
      {casa.metas.length === 0 ? <p className="faint">Nenhuma meta da casa.</p> : <ul className="casa-lista">
        {casa.metas.map((m) => (
          <li key={m.ref}>
            <p className="casa-nome"><b>{m.nome}</b> <span className="selo">{quem(m.autor)}</span></p>
            <p><span className="num">{moneyText(m.guardado)}</span> de <span className="num">{moneyText(m.alvo)}</span> <span className="faint">· {m.percentual}%</span></p>
            <span className={`meter${semZero(m.falta) ? " ok" : ""}`} aria-hidden="true"><span style={{ width: largura(m.percentual) }} /></span>
            <p className="faint">{semZero(m.falta) ? "Meta batida" : <>Faltam <span className="num">{moneyText(m.falta)}</span></>}{m.prazo && ` · até ${dia(m.prazo)}`}</p>
            <div className="fam-acoes">
              <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "mov", m: { tipo: "metas", ref: m.ref, sinal: "somar", titulo: `Guardar em ${m.nome}` } })}>Guardar</button>
              <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "mov", m: { tipo: "metas", ref: m.ref, sinal: "subtrair", titulo: `Tirar de ${m.nome}` } })}>Tirar</button>
              <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "meta", meta: m })}>Editar</button>
            </div>
          </li>
        ))}
      </ul>}
    </section>
    <section className="casa-sec" aria-labelledby="casa-orcs">
      <div className="casa-topo"><h2 id="casa-orcs">Orçamentos</h2><button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "orc" })}>Novo orçamento</button></div>
      <p className="faint">{monthTitle(casa.mes)}</p>
      {casa.orcamentos.length === 0 ? <p className="faint">Nenhum orçamento da casa.</p> : <ul className="casa-lista">
        {casa.orcamentos.map((o) => {
          const passou = ehNegativo(o.restante);
          return (
            <li key={o.ref}>
              <p className="casa-nome"><b>{o.categoria}</b> <span className="selo">{quem(o.autor)}</span></p>
              <p><span className="num">{moneyText(o.gasto)}</span> de <span className="num">{moneyText(o.limite)}</span></p>
              <span className={`meter${passou ? " warn" : ""}`} aria-hidden="true"><span style={{ width: largura((Number(o.gasto) / Number(o.limite)) * 100) }} /></span>
              <p className={passou ? "warn" : "faint"}>{passou ? <>Passou <span className="num">{moneyText(o.restante.slice(1))}</span></> : <>Restam <span className="num">{moneyText(o.restante)}</span></>}</p>
              <div className="fam-acoes">
                <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "mov", m: { tipo: "orcamentos", ref: o.ref, sinal: "somar", titulo: `Lançar gasto em ${o.categoria}` } })}>Lançar gasto</button>
                <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "mov", m: { tipo: "orcamentos", ref: o.ref, sinal: "subtrair", titulo: `Desfazer gasto em ${o.categoria}` } })}>Desfazer gasto</button>
                <button type="button" className="btn btn-ghost" onClick={() => abrir({ k: "orc", orc: o })}>Editar</button>
              </div>
            </li>
          );
        })}
      </ul>}
    </section>
  </>;

  // "Apagar" mora no diálogo de edição: ele fecha e abre a confirmação (dois diálogos nunca ficam empilhados).
  const pedirApagar = (tipo: Tipo, ref: string, nome: string) => () => abrir({ k: "apagar", tipo, ref, nome });
  // O diálogo e a confirmação saem do ÚLTIMO dado bom (`q.data`), não de `casa`: uma releitura que falha esconde só o corpo da lista, nunca o rascunho nem a escrita em voo.
  const diag = !uid || !q.data ? null : aberto?.k === "meta" ? <CasaMetaForm uid={uid} meta={aberto.meta} fechar={fechar} apagar={aberto.meta && pedirApagar("metas", aberto.meta.ref, aberto.meta.nome)} />
    : aberto?.k === "orc" ? <CasaOrcamentoForm uid={uid} orcamento={aberto.orc} fechar={fechar} apagar={aberto.orc && pedirApagar("orcamentos", aberto.orc.ref, aberto.orc.categoria)} />
    : aberto?.k === "mov" ? <CasaMovimento uid={uid} m={aberto.m} fechar={fechar} />
    : aberto?.k === "apagar" ? <Confirma titulo={`Apagar ${aberto.nome}?`} texto="Some para todos da família." botao="Apagar" pendente="Apagando…" fechar={fechar}
      agir={async () => {
        try { await apagar(aberto.tipo, aberto.ref); void reler(); } // o DELETE já deu certo: a releitura (5xx = ~3,4 s de retries; pendurada = nunca) não prende a confirmação
        catch (e) { setFalha(falhaCasa(e, aberto.tipo)); if (deveReler(e)) void reler(); }
      }} /> : null;

  return (
    <>
      <header className="page-head">
        <h1 id="page-title" tabIndex={-1}>{route("/casa").title}</h1>
      </header>
      <div className="page-grid">
        <section className="panel span-12 conta-perfil conta-familia fam-casa" aria-label="Casa da família">{corpo}<FalhaFamilia falha={falha} /><AvisoCasa.Provider value={setFalha}>{diag}</AvisoCasa.Provider></section>
      </div>
    </>
  );
}
