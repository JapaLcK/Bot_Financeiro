// O card de compras de /gastos: as saídas de um dia do calendário (`gastosAberto.dia`) ou de uma categoria
// (`gastosAberto.categoria`, o "Ver compras" do detalhe), na própria tela. O total é o do servidor, da mesma
// consulta que pinta o calendário ou o detalhe (em cache); a lista é a de /lancamentos com tipo=saida, e o que
// o total não soma (movimento interno, guardado, outra moeda) vem marcado pelo LancamentoLinha. Nada é somado aqui.
import { useEffect, useRef } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Lancamento } from "../lib/api-v2.gen";
import { isoDay, longDate, moneyText, monthName } from "../lib/format.js";
import { useActions } from "../lib/actions";
import { mesDe } from "../lib/store.js";
import { categoriasQuery, contasQuery, gastosCategoriaQuery, gastosQuery, lancamentosQuery } from "../lib/v2";
import type { DashState } from "../lib/types";
import { Frame } from "../parts/Frame";
import { estadoGastos } from "../parts/GastosEstado";
import { LancamentoLinha } from "../parts/LancamentoLinha";
import { Selos } from "../parts/Selos";

const marcada = (i: Lancamento) => i.interno || i.motivos.includes("outra_moeda"); // guardado implica interno

export function GastosCompras({ s, chegada }: { s: DashState; chegada: { current: boolean } }) {
  const { set, setFilter, go } = useActions();
  const g = s.gastosAberto!; // só é montado com o card aberto
  const dia = "dia" in g ? g.dia : null;
  const mes = mesDe(s);
  const cat = "categoria" in g ? g.categoria : s.filter.category; // no modo dia o card segue a categoria escolhida, como o calendário
  const geral = useQuery({ ...gastosQuery(mes), enabled: !cat });
  const daCat = useQuery({ ...gastosCategoriaQuery(mes, cat ?? ""), enabled: !!cat });
  const q = cat ? daCat : geral;
  const d = q.data;
  const lista = useInfiniteQuery(lancamentosQuery({ mes, dia: dia ?? undefined, tipo: "saida", categoria: cat ?? undefined })); // mes e dia sempre juntos: o cartão entra pelo mês da fatura
  const cats = useQuery(categoriasQuery);
  const contas = useQuery(contasQuery);
  const itens = lista.data?.pages.flatMap((p) => p.itens) ?? [];

  const estado = estadoGastos(q);
  const pronto = !estado && !lista.isPending; // total e lista na tela: o card já tem a altura final
  const ref = useRef<HTMLDivElement>(null);
  // No quadro seguinte: vindo do Resumo, o foco no título da página (App.tsx) é do efeito do pai, que roda depois deste.
  useEffect(() => {
    const f = requestAnimationFrame(() => ref.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(f);
  }, [dia ?? cat]);
  // O scroll espera o conteúdo: com o card ainda vazio o fim da página está mais perto e o pedido fica no mesmo lugar. Na chegada
  // (vindo do Resumo) o alinhamento é o do efeito de baixo: o scroll suave do roteador (volta ao topo) ainda corre, e "nearest"
  // mediria contra ele.
  useEffect(() => {
    const f = requestAnimationFrame(() => { if (!chegada.current) ref.current?.scrollIntoView({ block: "nearest" }); });
    return () => cancelAnimationFrame(f);
  }, [dia ?? cat, pronto]);
  // Na chegada o card vai ao início da janela e o Detalhe, acima dele, carrega depois e o empurraria para baixo da dobra: enquanto a
  // pessoa não mexer (roda, toque, tecla, clique), cada mudança de tamanho da coluna ou da grade realinha. O primeiro gesto encerra a chegada.
  useEffect(() => {
    if (!chegada.current) return;
    const col = ref.current!.parentElement!;
    const alinhar = () => { if (chegada.current) ref.current?.scrollIntoView({ block: "start" }); };
    const fim = () => { chegada.current = false; };
    const ro = new ResizeObserver(alinhar);
    ro.observe(col);
    ro.observe(col.parentElement!);
    const gestos = ["wheel", "touchstart", "keydown", "pointerdown"];
    gestos.forEach((e) => addEventListener(e, fim, { passive: true }));
    return () => { ro.disconnect(); gestos.forEach((e) => removeEventListener(e, fim)); };
  }, []);
  const fechar = () => {
    const origem = document.querySelector<HTMLElement>('[aria-controls="gastos-compras"][aria-expanded="true"]'); // lido antes de fechar: a célula do dia ou o "Ver compras"
    set({ gastosAberto: null });
    // A célula do dia pode ter ficado desabilitada (o dia não existe na categoria escolhida): focar nela não faz nada e o foco iria ao BODY.
    (origem && !(origem as HTMLButtonElement).disabled ? origem : document.querySelector<HTMLElement>("#w-calendario .cal-day:not(:disabled)"))?.focus();
  };
  const ver = () => {
    setFilter(dia ? { day: dia } : { category: cat, day: null });
    go("/lancamentos");
  };

  const nome = daCat.data?.categoria.nome;
  const [y, m] = mes.split("-").map(Number);
  const entrada = dia && d ? d.dias.find((x) => x.dia === dia) : undefined;
  const titulo = dia ? longDate(isoDay(dia)) : nome ? `${nome[0].toUpperCase()}${nome.slice(1)} · ${monthName(new Date(y, m - 1, 1))}` : "Compras";
  const rotulo = dia ? (dia > (d?.hoje ?? "") ? "Agendado para o dia" : "Gasto no dia") : "Gasto no mês";
  const total = dia ? entrada?.total : daCat.data?.total;
  const comTotal = dia ? !!entrada : Number(daCat.data?.compras) > 0;

  return (
    <div ref={ref} className="panel" role="region" id="gastos-compras" tabIndex={-1} aria-labelledby="w-compras-h" onKeyDown={(e) => { if (e.key === "Escape") fechar(); }}>
      <Frame id="compras" real title={titulo} aside={dia && cat && nome && <span className="tag-demo">Só {nome}</span>}>
        {estado ?? <>
          {total !== undefined ? <p className="w-lede">{rotulo} · <span className="num">{moneyText(total) ?? "—"}</span></p> : <p className="faint">Nenhum gasto neste dia.</p>}
          <Selos motivos={d?.motivos ?? []} />
          {lista.isPending && <p role="status" className="faint">Carregando…</p>}
          {lista.isError && !lista.data && (
            <div className="empty" role="alert">
              <p>Não deu para carregar.</p>
              <button type="button" className="btn retry btn-ghost" onClick={() => lista.refetch()}>Tentar de novo</button>
            </div>
          )}
          {lista.data && !itens.length && comTotal && <p className="faint">Nenhum lançamento encontrado.</p>}
          {itens.length > 0 && <ul className="compras-lista">{itens.map((item) => <LancamentoLinha key={item.id} item={item} categorias={cats.data?.categorias ?? []} contas={contas.data?.contas ?? []} />)}</ul>}
          {itens.some(marcada) && <p className="faint compras-nota">Movimentos entre contas, guardados e saídas em outra moeda aparecem marcados e ficam fora do total.</p>}
          {lista.hasNextPage && (
            <div className="compras-mais">
              {lista.isFetchNextPageError && <p role="alert">Não deu para carregar mais.</p>}
              <button type="button" className="btn btn-ghost" disabled={lista.isFetchingNextPage} onClick={() => lista.fetchNextPage()}>
                {lista.isFetchingNextPage ? "Carregando…" : lista.isFetchNextPageError ? "Tentar de novo" : "Carregar mais"}
              </button>
            </div>
          )}
        </>}
        <div className="compras-pe">
          <button type="button" className="link" onClick={ver}>Ver em Lançamentos</button>
          <button type="button" className="link" onClick={fechar}>Fechar</button>
        </div>
      </Frame>
    </div>
  );
}
