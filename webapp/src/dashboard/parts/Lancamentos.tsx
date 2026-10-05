import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Lancamento, QueryGet } from "../lib/api-v2.gen";
import type { DashState } from "../lib/types";
import { mesDe } from "../lib/store.js";
import { categoriasQuery, contasQuery, lancamentosQuery } from "../lib/v2";
import { isoDay, longDate } from "../lib/format.js";
import { Frame } from "./Frame";
import { Selos } from "./Selos";
import { LancamentoLinha, ORIGENS } from "./LancamentoLinha";
import { LancamentoForm } from "./LancamentoForm";
import { useLancamentoMutation } from "./useLancamentoMutation";

type Filtros = Omit<QueryGet["/lancamentos"], "cursor" | "mes">;
export function Lancamentos({ s }: { s: DashState }) {
  const [filtros, setFiltros] = useState<Filtros>({});
  const [busca, setBusca] = useState("");
  const [q, setQ] = useState("");
  const [form, setForm] = useState<"novo" | Lancamento | null>(null);
  const avisoRef = useRef<HTMLParagraphElement>(null);
  const voltar = useRef<HTMLElement | null>(null);
  const mes = mesDe(s);
  useEffect(() => { const t = window.setTimeout(() => setQ(busca.trim()), 250); return () => window.clearTimeout(t); }, [busca]);
  const curta = !!q && !q.split(/\s+/).some((t) => t.length >= 2);
  const lista = useInfiniteQuery(lancamentosQuery({ mes, ...filtros, q: !curta && q ? q : undefined }));
  const cats = useQuery(categoriasQuery);
  const contas = useQuery(contasQuery);
  const escrita = useLancamentoMutation();
  const itens = lista.data?.pages.flatMap((p) => p.itens) ?? [];
  const grupos = new Map<string, Lancamento[]>();
  for (const item of itens) grupos.set(item.data, [...(grupos.get(item.data) ?? []), item]);
  const abrir = (item: "novo" | Lancamento) => { voltar.current = document.activeElement as HTMLElement; setForm(item); };
  const fechar = () => {
    setForm(null);
    window.setTimeout(() => { if (voltar.current?.isConnected) voltar.current.focus(); else avisoRef.current?.focus(); }, 0);
  };
  // Depois de 409 a resposta atual governa os campos sem apagar o rascunho.
  const atual = form && form !== "novo" ? itens.find((i) => i.id === form.id) : undefined;
  const filtro = <K extends keyof Filtros>(k: K, v: Filtros[K]) => setFiltros((f) => ({ ...f, [k]: v || undefined }));
  return <Frame id="lancamentos" title="Lançamentos" className="lancamentos" real><div className="lanc-content">
    <div className="lanc-toolbar">
      <button className="btn btn-primary" type="button" disabled={escrita.pendente} onClick={() => abrir("novo")}>Lançar na Carteira</button>
      <button className="btn btn-ghost" type="button" disabled={escrita.pendente || lista.isFetching} onClick={() => escrita.atualizar()}>Atualizar lista</button>
    </div>
    <p ref={avisoRef} className="lanc-aviso" role="status" tabIndex={-1}>{escrita.aviso}</p>
    {escrita.bloqueado && <p role="alert">Confira a lista atualizada antes de repetir a gravação.</p>}
    <div className="lanc-filtros">
      <label className="lanc-busca">Buscar<input className="field" type="search" maxLength={200} value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Descrição ou nota" /></label>
      <label>Origem<select className="field" value={filtros.origem ?? ""} onChange={(e) => filtro("origem", e.target.value as Filtros["origem"])}><option value="">Todas as origens</option>{Object.entries(ORIGENS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label>Tipo<select className="field" value={filtros.tipo ?? ""} onChange={(e) => filtro("tipo", e.target.value as Filtros["tipo"])}><option value="">Entradas e saídas</option><option value="entrada">Entrada</option><option value="saida">Saída</option></select></label>
      <label>Categoria<select className="field" disabled={!cats.data} value={filtros.categoria ?? ""} onChange={(e) => filtro("categoria", e.target.value)}><option value="">Todas as categorias</option>{cats.data?.categorias.map((c) => <option key={c.chave} value={c.chave}>{c.nome}</option>)}</select></label>
      <label>Conta<select className="field" disabled={!contas.data} value={filtros.conta ?? ""} onChange={(e) => filtro("conta", e.target.value ? Number(e.target.value) : undefined)}><option value="">Todas as contas</option>{contas.data?.contas.map((c) => <option key={c.id} value={c.id}>{c.instituicao ?? "Banco"}{c.nome ? ` · ${c.nome}` : ""}</option>)}</select></label>
    </div>
    {cats.isError && <p role="alert">Não foi possível carregar as categorias. <button className="btn btn-quiet" onClick={() => cats.refetch()}>Tentar categorias novamente</button></p>}
    {contas.isError && <p role="alert">Não foi possível carregar as contas. <button className="btn btn-quiet" onClick={() => contas.refetch()}>Tentar contas novamente</button></p>}
    {curta ? <p role="status">Use pelo menos uma palavra de 2 ou mais caracteres para buscar.</p> : q && <p className="w-lede">Busca no histórico disponível: “{q}”.</p>}
    <Selos motivos={lista.data?.pages[0].motivos ?? []} />
    {lista.isPending && <p role="status">Carregando lançamentos…</p>}
    {lista.isError && <p role="alert">{lista.data ? "Não foi possível atualizar os lançamentos." : "Não foi possível carregar os lançamentos."} <button className="btn btn-ghost" onClick={() => lista.refetch()}>Tentar novamente</button></p>}
    {lista.data && !itens.length && <p className="empty">Nenhum lançamento encontrado.</p>}
    {[...grupos].map(([dia, linhas]) => <section key={dia} className="lanc-dia" aria-label={longDate(isoDay(dia))}>
      <h3>{longDate(isoDay(dia))}</h3>
      <ul>{linhas.map((item) => <LancamentoLinha key={item.id} item={item} categorias={cats.data?.categorias ?? []} contas={contas.data?.contas ?? []} abrir={() => abrir(item)} />)}</ul>
    </section>)}
    {lista.hasNextPage && <div className="lanc-mais">
      {lista.isFetchNextPageError && <p role="alert">Não foi possível carregar mais lançamentos.</p>}
      <button type="button" className="btn btn-ghost" disabled={lista.isFetching || escrita.pendente} onClick={() => lista.fetchNextPage()}>{lista.isFetchingNextPage ? "Carregando…" : lista.isFetchNextPageError ? "Tentar novamente" : "Carregar mais"}</button>
    </div>}
    {form && <LancamentoForm key={form === "novo" ? "novo" : form.id} item={form === "novo" ? undefined : atual ?? form} indisponivel={form !== "novo" && !!lista.data && !atual} categorias={cats.data?.categorias ?? []} contas={contas.data?.contas ?? []} escrita={escrita} fechar={fechar} />}
  </div></Frame>;
}
