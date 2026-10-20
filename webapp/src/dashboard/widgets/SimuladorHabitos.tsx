// Simulador real do gasto do dia a dia (POST /api/v2/simulador/habitos): o servidor calcula, a tela só mostra o texto
// da API (moneyText); Number() só desenha a curva. O arraste é rascunho visual; o corte só vai ao soltar (evento nativo `change`).
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CategoriaEstimada, RotasPost, SimuladorHabitos as Resposta } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { ErroApi, escrever, meQuery, perfilQuery, previsaoQuery, usePlan } from "../lib/v2";
import { CRIAR_SENHA } from "../parts/Entrada";
import { comCardPrevisao } from "../parts/Board";
import { Frame } from "../parts/Frame";
import { ConvitePro, ddmm } from "./Compromissos";
import { useListaDaPagina } from "./ReceitasDoBanco";
import { CurvaComparada } from "./CurvaComparada";

const MAX_NO_RESUMO = 5;
const reais = (s: string | null) => (s === null ? "indisponível" : moneyText(s) ?? "indisponível");
type Cortes = Record<string, number>;

function Alavanca({ c, valor, mexe, solta }: { c: CategoriaEstimada; valor: number; mexe: (v: number) => void; solta: (v: number) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const ultimo = useRef(solta);
  ultimo.current = solta;
  // `change` nativo: dispara ao soltar (mouse/toque, mesmo fora do controle) e a cada tecla; o onChange do React é o `input`.
  useEffect(() => {
    const el = ref.current!, f = () => ultimo.current(Number(el.value));
    el.addEventListener("change", f);
    return () => el.removeEventListener("change", f);
  }, []);
  return (
    <label className="lever lever-habito">
      <span className="lever-name">{c.nome}</span>
      <span className="lever-base faint num">{reais(c.despesa_mensal)}/mês</span>
      <input ref={ref} className="range" type="range" min={0} max={100} step={10} value={valor} style={{ ["--p" as string]: `${valor}%` }}
        aria-valuetext={valor ? `cortar ${valor}%` : "sem corte"} onChange={(e) => mexe(Number(e.target.value))} />
      <span className={`lever-cut num ${valor > 0 ? "" : "faint"}`}>{valor > 0 ? `−${valor}%` : "0%"}</span>
    </label>
  );
}

function Resultado({ d, full }: { d: Resposta; full: boolean }) {
  const pior = d.com_corte.pior_dia;
  return (
    <>
      <dl className="sim-facts">
        <div><dt>Sobra por mês</dt><dd className="num gain">{reais(d.economia_mensal)}</dd></div>
        <div><dt>Saldo no dia {d.dias}</dt><dd className="num">{reais(d.com_corte.saldo_final)}</dd></div>
        <div><dt>Menor saldo</dt><dd className={`num ${pior?.saldo.startsWith("-") ? "warn" : ""}`}>{pior ? `${reais(pior.saldo)} em ${ddmm(pior.data)}` : "indisponível"}</dd></div>
      </dl>
      {full && (
        <>
          <ul className="premissas">{d.cortes.map((c) => <li key={c.chave}>{c.nome}: −{c.percentual}% = {reais(c.economia_mensal)} por mês</li>)}</ul>
          <CurvaComparada antes={d.atual.trajetoria} depois={d.com_corte.trajetoria} rotulos={["Sem o corte", "Com o corte"]} drawKey="habitos" />
        </>
      )}
    </>
  );
}

const PRIMEIRO = <p className="sim-prompt">Arraste um controle para ver quanto sobra por mês.</p>;
const GENERICO = "Não deu para calcular agora.";

export function SimuladorHabitos({ full = false }: { s?: unknown; full?: boolean }) {
  const pro = usePlan() === "pro";
  const qc = useQueryClient();
  // No Resumo com o card de previsão, a estimativa é a que ELE leu (mesma regra da pilha de avisos): um 2º observador da chave padrão guardaria o dado do plano antigo.
  const perfil = useQuery({ ...perfilQuery, enabled: pro && !full, subscribed: pro && !full });
  const comCard = !full && !!perfil.data && comCardPrevisao(perfil.data.perfil ?? "padrao", "pro");
  const sozinho = pro && (full || (!perfil.isPending && !comCard));
  const prev = useQuery({ ...previsaoQuery(null), enabled: sozinho, subscribed: sozinho });
  const [doCard] = useListaDaPagina();
  const dados = comCard ? doCard : prev.data;
  const [rascunho, setRascunho] = useState<Cortes>({});
  const [cortes, setCortes] = useState<Cortes>({});
  const est = dados?.estimativa_variavel ?? null;
  const cats = (est?.categorias ?? []).slice(0, full ? undefined : MAX_NO_RESUMO);
  // Só chaves da estimativa atual, na ordem dela, e só com corte: sem nenhum, não há POST nem resultado.
  const lista = cats.filter((c) => cortes[c.chave] > 0).map((c) => ({ categoria: c.chave, percentual: cortes[c.chave] }));
  const corpo: RotasPost["/simulador/habitos"]["corpo"] | null = lista.length ? { cortes: lista } : null;
  const q = useQuery({
    queryKey: ["simulador-habitos", dados?.calculado_em, corpo], // a versão do snapshot: previsão relida com as mesmas chaves não reaproveita o resultado velho
    queryFn: ({ signal }) => escrever<Resposta>("POST", "/simulador/habitos", corpo, signal),
    enabled: pro && !!corpo, retry: false, gcTime: 0, staleTime: Infinity, refetchOnWindowFocus: false, networkMode: "always",
    placeholderData: keepPreviousData, // a chave abandonada é abortada e a resposta de outra chave nunca aparece na atual
  });
  const e = q.error instanceof ErroApi ? q.error : q.error ? new ErroApi(0, "network") : null;
  const mudou = e?.status === 422 && !!e.details?.some((x) => x.type === "categoria_fora_da_estimativa");
  useEffect(() => {
    if (!e) return;
    if (e.status === 401 || e.status === 402) void qc.refetchQueries({ queryKey: meQuery.queryKey }); // o portão decide
    if (mudou) void qc.invalidateQueries({ queryKey: ["previsao"] }); // sem retentativa do POST: a chave só muda se a lista mudar
  }, [e, mudou, qc]);

  const titulo = full ? "Seus gastos do dia a dia" : "Simulador";
  const id = full ? "habitos-alavancas" : "simulador";
  if (!pro) {
    const convite = <Frame id={id} title={titulo} real><ConvitePro texto="O simulador é do Pro." /></Frame>;
    return full ? <div className="panel span-12">{convite}</div> : convite;
  }

  let alavancas: ReactNode;
  if (comCard ? !doCard : prev.isPending) alavancas = <p className="w-lede">Carregando…</p>;
  else if (!comCard && prev.isError) alavancas = <div className="empty" role="alert"><p>Não deu para ler seus gastos agora.</p><button type="button" className="btn btn-ghost retry" onClick={() => void prev.refetch()}>Tentar de novo</button></div>;
  else if (!est) alavancas = <p className="w-lede">Ainda não há histórico para estimar o gasto do dia a dia.</p>;
  else alavancas = (
    <div className="levers">
      {cats.map((c) => (
        <Alavanca key={c.chave} c={c} valor={rascunho[c.chave] ?? 0}
          mexe={(v) => setRascunho((r) => ({ ...r, [c.chave]: v }))}
          solta={(v) => setCortes((x) => ({ ...x, [c.chave]: v }))} />
      ))}
    </div>
  );

  let saida: ReactNode = PRIMEIRO;
  if (corpo && e) {
    saida = e.code === "pro_required" ? <ConvitePro texto="O simulador é do Pro." />
      : e.code === "password_required" ? <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>
      : <p role="alert" className="warn">{mudou ? "Seus gastos do dia a dia mudaram. Ajuste de novo." : GENERICO}</p>;
  } else if (corpo && q.data) saida = <div className="hab-res" aria-busy={q.isPlaceholderData}><Resultado d={q.data} full={full} /></div>;
  else if (corpo) saida = <p className="w-lede">Calculando…</p>;

  if (!full) return <Frame id={id} title={titulo} real>{alavancas}<div className="sim-out" aria-live="polite">{est && saida}</div></Frame>;
  return (
    <>
      <div className="panel span-5"><Frame id={id} title={titulo} real>{alavancas}</Frame></div>
      <div className="panel span-7"><Frame id="habitos-resultado" title="O que muda" real><div aria-live="polite">{est ? saida : null}</div></Frame></div>
    </>
  );
}
