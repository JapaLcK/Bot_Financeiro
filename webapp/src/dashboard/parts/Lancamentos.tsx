import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Lancamento, NovoLancamento, QueryGet } from "../lib/api-v2.gen";
import type { DashState } from "../lib/types";
import { useActions } from "../lib/actions";
import { escolherMes, mesDe } from "../lib/store.js";
import { categoriasQuery, contasQuery, lancamentosQuery } from "../lib/v2";
import { isoDay, longDate } from "../lib/format.js";
import { ATALHOS, diasDoExtrato, rotuloDia } from "../lib/extrato-dias";
import { hojeNoFuso } from "../lib/metas-textos";
import { Frame } from "./Frame";
import { Selos } from "./Selos";
import { LancamentoLinha, ORIGENS } from "./LancamentoLinha";
import { LancamentoForm } from "./LancamentoForm";
import { useLancamentoMutation } from "./useLancamentoMutation";

// Categoria e dia são a escolha global (`s.filter`): "Para onde vai" e o calendário escrevem lá e abrem esta tela.
type Filtros = Omit<QueryGet["/lancamentos"], "cursor" | "mes" | "categoria" | "dia">;
export function Lancamentos({ s }: { s: DashState }) {
  const [filtros, setFiltros] = useState<Filtros>({});
  const [busca, setBusca] = useState("");
  const [q, setQ] = useState("");
  const [painel, setPainel] = useState(false);
  const [form, setForm] = useState<"novo" | Lancamento | null>(null);
  const [draft, setDraft] = useState<NovoLancamento>();
  const avisoRef = useRef<HTMLParagraphElement>(null);
  const voltar = useRef<HTMLElement | null>(null);
  const mes = mesDe(s);
  const { setFilter } = useActions();
  const categoria = s.filter.category;
  // O dia é de um mês: no outro ele esvaziaria a lista (e a fatura do cartão entra pelo mês dela).
  const dia = s.filter.day?.startsWith(mes) ? s.filter.day : null;
  useEffect(() => { const t = window.setTimeout(() => setQ(busca.trim()), 250); return () => window.clearTimeout(t); }, [busca]);
  const curta = !!q && !q.split(/\s+/).some((t) => t.length >= 2);
  const historico = !curta && !!q;
  // `undefined`, não `null`: sem escolha a chave é a de `lancamentosQuery({ mes })`, que a conferência (useLancamentoMutation) refaz.
  const lista = useInfiniteQuery(lancamentosQuery({ mes, ...filtros, categoria: categoria ?? undefined, dia: dia ?? undefined, q: historico ? q : undefined }));
  const cats = useQuery(categoriasQuery);
  const contas = useQuery(contasQuery);
  const escrita = useLancamentoMutation();
  const itens = lista.data?.pages.flatMap((p) => p.itens) ?? [];
  const hoje = hojeNoFuso();
  const abrir = (item: "novo" | Lancamento, rascunho?: NovoLancamento) => { setDraft(rascunho); voltar.current = document.activeElement as HTMLElement; setForm(item); };
  const fechar = () => {
    setForm(null);
    window.setTimeout(() => { if (voltar.current?.isConnected) voltar.current.focus(); else avisoRef.current?.focus(); }, 0);
  };
  const c = escrita.conferencia;
  const visaoConferivel = !!c && c.fase === "pronta" && c.alvo === mes && !busca && !q && !categoria && !dia && !Object.values(filtros).some(Boolean) && !!lista.data && !lista.isFetching && !lista.isError;
  const conferir = async (alvo?: string) => {
    const resultado = await escrita.atualizar(alvo);
    if (typeof resultado !== "string") return;
    setBusca(""); setQ(""); setFiltros({}); setFilter({ category: null, day: null }); escolherMes(resultado); setForm(null);
    window.setTimeout(() => avisoRef.current?.focus(), 0);
  };
  // Depois de 409 a resposta atual governa os campos sem apagar o rascunho.
  const atual = form && form !== "novo" ? itens.find((i) => i.id === form.id) : undefined;
  const filtro = <K extends keyof Filtros>(k: K, v: Filtros[K]) => setFiltros((f) => ({ ...f, [k]: v || undefined }));
  // Fichas removíveis do que os chips não mostram; chip que representa o par (tipo, origem) dispensa as duas.
  const atalho = ATALHOS.some(([t, o]) => t === (filtros.tipo ?? "") && o === (filtros.origem ?? ""));
  const catNome = cats.data?.categorias.find((x) => x.chave === categoria)?.nome ?? categoria;
  const conta = contas.data?.contas.find((x) => x.id === filtros.conta);
  const fichas: { k: string; nome: string; tirar: () => void }[] = [];
  if (dia) fichas.push({ k: "dia", nome: `dia ${Number(dia.slice(8))}`, tirar: () => setFilter({ day: null }) });
  if (categoria) fichas.push({ k: "categoria", nome: catNome!, tirar: () => setFilter({ category: null }) });
  if (filtros.conta) fichas.push({ k: "conta", nome: conta ? `${conta.instituicao ?? "Banco"}${conta.nome ? ` · ${conta.nome}` : ""}` : "Conta", tirar: () => filtro("conta", undefined) });
  if (!atalho && filtros.tipo) fichas.push({ k: "tipo", nome: filtros.tipo === "entrada" ? "Entradas" : "Saídas", tirar: () => filtro("tipo", undefined) });
  if (!atalho && filtros.origem) fichas.push({ k: "origem", nome: ORIGENS[filtros.origem], tirar: () => filtro("origem", undefined) });
  const visiveis = fichas.filter((f) => f.k === "dia" || !painel);
  return <Frame id="lancamentos" title="Lançamentos" className="lancamentos" real><div className="lanc-content">
    <div className="lanc-toolbar">
      <button className="btn btn-primary" type="button" disabled={escrita.pendente} onClick={() => abrir("novo")}>Lançar na Carteira</button>
      <button className="btn btn-ghost" type="button" disabled={escrita.pendente || lista.isFetching} onClick={() => conferir()}>Atualizar lista</button>
    </div>
    <p ref={avisoRef} className="lanc-aviso" role="status" tabIndex={-1}>{escrita.aviso}</p>
    {escrita.bloqueado && <div className="lanc-conferencia">
      <p role="alert">A gravação pode ter sido concluída. Confira as páginas necessárias; não aparecer na primeira página não prova ausência.</p>
      {c?.item && <p>{c.item.descricao} · {c.item.id}{c.item.fatura ? ` · Fatura ${c.item.fatura}` : ""}</p>}
      {c?.escrita.acao === "criar" && !c.escrita.corpo.data && <p>A data vazia usa hoje no servidor. Se o mês virou desde o envio, confira também o período anterior.</p>}
      <div className="lanc-toolbar">
        <button className="btn btn-ghost" disabled={escrita.pendente || c?.fase === "carregando"} onClick={() => conferir()}>Retomar conferência</button>
        {c && c.meses.length > 1 && c.meses.map((m) => <button key={m} className="btn btn-ghost" disabled={c.fase === "carregando"} onClick={() => conferir(m)}>Conferir {m}</button>)}
        <button className="btn btn-primary" disabled={!visaoConferivel || !!c?.meses.some((m) => !c.vistos.includes(m))} onClick={() => escrita.confirmar(visaoConferivel)}>Conferi os lançamentos</button>
      </div>
    </div>}
    {escrita.rascunho && <div className="lanc-toolbar"><button className="btn btn-ghost" onClick={() => abrir("novo", escrita.rascunho)}>Voltar ao rascunho</button>{!escrita.bloqueado && <button className="btn btn-quiet" onClick={escrita.descartarRascunho}>Descartar rascunho</button>}</div>}
    <div className="lanc-busca-linha">
      <label className="lanc-busca"><i className="ph ph-magnifying-glass" aria-hidden="true" /><input className="field" type="search" aria-label="Buscar lançamento" maxLength={200} value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar" /></label>
      <button type="button" className="icon-btn" aria-label="Filtros" aria-expanded={painel} aria-controls="lanc-filtros" data-ativo={fichas.length > 0} onClick={() => setPainel(!painel)}><i className="ph ph-sliders-horizontal" aria-hidden="true" /></button>
    </div>
    <div className="lanc-atalhos">{ATALHOS.map(([t, o, nome]) => <button key={nome} type="button" className="chip" aria-pressed={(filtros.tipo ?? "") === t && (filtros.origem ?? "") === o} onClick={() => setFiltros((f) => ({ ...f, tipo: t || undefined, origem: o || undefined }))}>{nome}</button>)}</div>
    <div id="lanc-filtros" className="lanc-filtros" hidden={!painel}>
      <label>Origem<select className="field" value={filtros.origem ?? ""} onChange={(e) => filtro("origem", e.target.value as Filtros["origem"])}><option value="">Todas as origens</option>{Object.entries(ORIGENS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Tipo<select className="field" value={filtros.tipo ?? ""} onChange={(e) => filtro("tipo", e.target.value as Filtros["tipo"])}><option value="">Entradas e saídas</option><option value="entrada">Entrada</option><option value="saida">Saída</option></select></label>
      <label>Categoria<select className="field" disabled={!cats.data && !categoria} value={categoria ?? ""} onChange={(e) => setFilter({ category: e.target.value || null })}><option value="">Todas as categorias</option>{categoria && !cats.data?.categorias.some((c) => c.chave === categoria) && <option value={categoria}>{categoria[0].toUpperCase() + categoria.slice(1)}</option>}{cats.data?.categorias.map((c) => <option key={c.chave} value={c.chave}>{c.nome}</option>)}</select></label>
      <label>Conta<select className="field" disabled={!contas.data} value={filtros.conta ?? ""} onChange={(e) => filtro("conta", e.target.value ? Number(e.target.value) : undefined)}><option value="">Todas as contas</option>{contas.data?.contas.map((c) => <option key={c.id} value={c.id}>{c.instituicao ?? "Banco"}{c.nome ? ` · ${c.nome}` : ""}</option>)}</select></label>
    </div>
    {visiveis.length > 0 && <div className="ledger-chips">{visiveis.map(({ k, nome, tirar }) => <button key={k} type="button" className="chip" aria-pressed="true" onClick={tirar}>{nome}<i className="ph ph-x x" aria-hidden="true" /><span className="sr-only">remover filtro</span></button>)}</div>}
    {cats.isError && <p role="alert">Não foi possível carregar as categorias. <button className="btn btn-quiet" onClick={() => cats.refetch()}>Tentar categorias novamente</button></p>}
    {contas.isError && <p role="alert">Não foi possível carregar as contas. <button className="btn btn-quiet" onClick={() => contas.refetch()}>Tentar contas novamente</button></p>}
    {curta ? <p role="status">Use pelo menos uma palavra de 2 ou mais caracteres para buscar.</p> : q && <p className="w-lede">Busca no histórico disponível: “{q}”.</p>}
    <Selos motivos={lista.data?.pages[0].motivos ?? []} />
    {lista.isPending && <p role="status">Carregando lançamentos…</p>}
    {lista.isError && <p role="alert">{lista.data ? "Não foi possível atualizar os lançamentos." : "Não foi possível carregar os lançamentos."} <button className="btn btn-ghost" onClick={() => lista.refetch()}>Tentar novamente</button></p>}
    {lista.data && !itens.length && <p className="empty">Nenhum lançamento encontrado.</p>}
    {diasDoExtrato(itens, !lista.hasNextPage).map(({ dia, itens: linhas, total }) => <section key={dia} className="lanc-dia" aria-label={`${longDate(isoDay(dia))}${historico ? ` ${dia.slice(0, 4)}` : ""}`}>
      <h3><span>{rotuloDia(dia, hoje)}</span>{total && <span className="sr-only">, </span>}{total && <span className={`num ${total.ganho ? "gain" : ""}`}>{total.texto}</span>}</h3>
      <ul>{linhas.map((item) => <LancamentoLinha key={item.id} item={item} semData historico={historico} categorias={cats.data?.categorias ?? []} abrir={() => abrir(item)} />)}</ul>
    </section>)}
    {lista.hasNextPage && <div className="lanc-mais">
      {lista.isFetchNextPageError && <p role="alert">Não foi possível carregar mais lançamentos.</p>}
      <button type="button" className="btn btn-ghost" disabled={lista.isFetching || escrita.pendente} onClick={() => lista.fetchNextPage()}>{lista.isFetchingNextPage ? "Carregando…" : lista.isFetchNextPageError ? "Tentar novamente" : "Carregar mais"}</button>
    </div>}
    {form && <LancamentoForm key={form === "novo" ? "novo" : form.id} item={form === "novo" ? undefined : atual ?? form} indisponivel={form !== "novo" && !!lista.data && !atual} categorias={cats.data?.categorias ?? []} contas={contas.data?.contas ?? []} escrita={escrita} fechar={fechar} conferir={() => conferir()} rascunho={draft} mes={mes} historico={historico} />}
  </div></Frame>;
}
