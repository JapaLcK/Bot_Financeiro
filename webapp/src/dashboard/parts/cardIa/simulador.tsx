import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { uidValido } from "../../conta/api";
import type { CategoriaEstimada, Previsao, RotasPost, SimuladorHabitos } from "../../lib/api-v2.gen";
import { existe } from "../../lib/extrato-dias";
import { moneyText } from "../../lib/format.js";
import { obj } from "../../lib/ia-cards";
import { DEMO, ErroApi, escrever, sessaoDe } from "../../lib/v2";
import { SESSAO } from "../../lib/conversaIa";
import { Alavanca, GENERICO, MUDOU, Resultado, de } from "../../widgets/SimuladorHabitos";
import { naConta, useContaTrocada } from "./naConta";
import { Sub } from "./previsao";

// O card `simulador` do chat. Dois modos, como o do app (app/src/features/painel/widgets.tsx): com `simulador_habitos` no bloco a pessoa PEDIU um corte ("simula cortar
// 30% do mercado") e o servidor já calculou: só o resultado, sem controle e sem requisição. Sem ele, um controle por categoria da `previsao` DO BLOCO (a foto): soltar
// o controle manda UM POST /simulador/habitos e a tela mostra o que o servidor devolveu. O servidor lê a estimativa da SESSÃO, não deste card, e a categoria
// (`mercado`…) existe em toda conta: com a sessão de outra conta, o POST traria os números DELA. Por isso a conta é conferida antes e depois (naConta.ts).
export const SEM_PLANO = "Simulador indisponível no seu plano."; // igual ao do app (simuladorCompra.ts; card_ia_paridade.test.mjs compara)
type Cortes = Record<string, number>;
type Recursos = { previsao: Previsao; simulador_habitos?: SimuladorHabitos | null };

// Dinheiro que o card desenha: texto decimal do servidor e finito (a curva faz `Number()`, e "1e999" viraria ∞).
const din = (x: unknown) => moneyText(x) !== null && Number.isFinite(Number(x));
const pontos = (x: unknown) => Array.isArray(x) && x.every((p) => obj(p) && typeof p.data === "string" && existe(p.data) && (p.saldo === null || din(p.saldo)));
const resumo = (x: unknown) => obj(x) && (x.saldo_final === null || din(x.saldo_final)) && pontos(x.trajetoria)
  && (x.pior_dia === null || (obj(x.pior_dia) && typeof x.pior_dia.data === "string" && existe(x.pior_dia.data) && din(x.pior_dia.saldo)));
/** O `SimuladorHabitos` do bloco e o da resposta do POST: uma régua só. O que o `Resultado` desenha como texto ou número. */
export const contratoCorte = (d: unknown) => obj(d) && din(d.economia_mensal) && Number.isInteger(d.dias) && resumo(d.com_corte) && resumo(d.atual)
  && Array.isArray(d.cortes) && d.cortes.every((c) => obj(c) && typeof c.chave === "string" && typeof c.nome === "string" && Number.isInteger(c.percentual) && din(c.economia_mensal));
export const contratoSimulador = (r: unknown) => {
  const p = (r as { previsao?: unknown } | null)?.previsao, h = (r as { simulador_habitos?: unknown } | null)?.simulador_habitos;
  return obj(p) && typeof p.calculado_em === "string"
    && (p.estimativa_variavel === null || (obj(p.estimativa_variavel) && Array.isArray(p.estimativa_variavel.categorias)
      && p.estimativa_variavel.categorias.every((c) => obj(c) && typeof c.chave === "string" && c.chave !== "" && typeof c.nome === "string" && din(c.despesa_mensal))))
    && (h == null || contratoCorte(h));
};

// Cada soltar custa 2 leituras do perfil (1 antes do POST, 1 depois; o limite é 60/min e com 429 a conversa inteira some): a chave que o passo seguinte do teclado troca é
// abortada por esta espera sem ler perfil nem mandar POST. O mouse paga 300 ms.
const ESPERA_MS = 300;
const espera = (ms: number, s: AbortSignal) => new Promise<void>((ok, no) => {
  const corta = () => { clearTimeout(t); no(new DOMException("Aborted", "AbortError")); }, t = setTimeout(() => { s.removeEventListener("abort", corta); ok(); }, ms);
  if (s.aborted) corta(); else s.addEventListener("abort", corta, { once: true });
});
const mudou = (e: ErroApi) => e.status === 422 && !!e.details?.some((x) => x.type === "categoria_fora_da_estimativa");
// A ordem do `lib/v2.ts`: o plano (403 com código) antes da sessão (401, ou 403 sem código), e o 422 de categoria é "ajuste de novo" (o bloco é foto: sem reler a previsão).
const textoDoErro = (e: unknown) => !(e instanceof ErroApi) ? GENERICO : e.code === "pro_required" ? SEM_PLANO : sessaoDe(e) ? SESSAO : mudou(e) ? MUDOU : GENERICO;

function Controles({ cats, calculado, uid }: { cats: CategoriaEstimada[]; calculado: string; uid: number }) {
  const qc = useQueryClient(), raiz = useRef<HTMLDivElement>(null);
  const [rascunho, setRascunho] = useState<Cortes>({}), [cortes, setCortes] = useState<Cortes>({}); // o arraste só mexe no rascunho; o corte vale ao soltar
  // Só categorias do bloco, na ordem dele, e só com corte: sem nenhum, não há POST nem resultado.
  const lista = cats.filter((c) => de(cortes, c.chave) > 0).map((c) => ({ categoria: c.chave, percentual: de(cortes, c.chave) }));
  const corpo: RotasPost["/simulador/habitos"]["corpo"] | null = lista.length ? { cortes: lista } : null;
  // Chave ["conta", assunto, uid] (3 elementos, uid por último): a forma que o main.tsx reconhece como "da conta anterior". Sem `placeholderData`: a chave nova nasce sem número, então
  // o resultado do corte antigo nunca fica ao lado do novo; a chave abandonada perde o observador e o React Query aborta o POST. `retry: false`: soltar de novo é a retentativa.
  const q = useQuery({
    queryKey: ["conta", `card-ia-sim:${calculado}:${JSON.stringify(corpo)}`, uid],
    queryFn: async ({ signal }) => {
      await espera(ESPERA_MS, signal);
      return naConta(qc, uid, signal, async (s) => {
        const r = await escrever<SimuladorHabitos>("POST", "/simulador/habitos", corpo, s);
        if (!contratoCorte(r)) throw new Error("corte fora do contrato");
        return r;
      });
    },
    enabled: !!corpo, retry: false, gcTime: 0, staleTime: Infinity, refetchOnWindowFocus: false, networkMode: "always", meta: { foto: true },
  });
  if (useContaTrocada(q.error)) return null; // a conta mudou: o perfil relido apaga a conversa, e o card some com ela
  // O botão some ao zerar: o foco vai para o 1º controle, não para o <body>.
  const zerar = () => { setRascunho({}); setCortes({}); raiz.current?.querySelector("input")?.focus(); };
  return (
    <>
      <div ref={raiz} className="levers">
        {cats.map((c) => <Alavanca key={c.chave} c={c} valor={de(rascunho, c.chave)} mexe={(v) => setRascunho((r) => ({ ...r, [c.chave]: v }))} solta={(v) => setCortes((x) => ({ ...x, [c.chave]: v }))} />)}
      </div>
      <div aria-live="polite">
        {corpo && (q.error ? <p role="alert" className="warn">{textoDoErro(q.error)}</p>
          : q.data ? <div className="hab-res"><Resultado d={q.data} full /></div>
          : <p className="w-lede" aria-busy="true">Calculando…</p>)}
      </div>
      {(corpo || Object.values(rascunho).some((v) => v > 0)) && <button type="button" className="btn btn-ghost" onClick={zerar}>Zerar simulação</button>}
    </>
  );
}

export function CardSimulador({ r, uid }: { r: Recursos; uid?: number }) {
  if (r.simulador_habitos) return <div className="hab-res"><Resultado d={r.simulador_habitos} full /></div>; // `hab-res`: o respiro entre as peças do resultado, como no widget
  const est = r.previsao.estimativa_variavel;
  if (!est) return <p className="ci-vazio">Ainda sem histórico para estimar.</p>;
  if (DEMO || !uidValido(uid)) return <ul className="ci-subs">{est.categorias.map((c) => <Sub key={c.chave} nome={c.nome} valor={`${moneyText(c.despesa_mensal)}/mês`} />)}</ul>; // sem dono do card não há o que conferir: só a lista
  return <Controles cats={est.categorias} calculado={r.previsao.calculado_em} uid={uid} />;
}
