// Receitas que o servidor achou no extrato (`receitas_do_banco` da Previsão): só a que o usuário
// confirma entra na conta. Duas pontas com a mesma mutationKey (então `useIsMutating` trava as duas juntas):
// as PENDENTES moram na pilha de avisos do topo (`itensDeReceita`, montada por parts/AvisosDoTopo.tsx, em `/` e em `/previsao`)
// e as RESPONDIDAS, com o Desfazer, na seção "Receitas na previsão" no fim da `/previsao` (`RespondidasDoBanco`).
// Sem nada visível, a marcação sr-only fica montada para o anúncio e o foco do resultado. Sim/Não/Desfazer = POST /previsao/receita;
// sem atualização otimista: a lista na tela é sempre a última resposta do servidor. O nome é texto livre do pagador:
// só como texto do React, truncado no CSS.
import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { useIsMutating, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Previsao, ReceitaDoBanco, RespostaReceitaIn } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { apiPost } from "../lib/v2";
import { Frame } from "../parts/Frame";
import type { AvisoDaPilha } from "../parts/PilhaAvisos";

const CHAVE = ["previsao", "receita"];
const CHIP = { confirmada: "Confirmada", negada: "Recusada" } as const;
type Resposta = RespostaReceitaIn["resposta"];
// O status que a lista passa a ter quando o POST pegou, e o que o leitor de tela ouve.
const STATUS: Record<Resposta, string> = { sim: "confirmada", nao: "negada", desfazer: "pendente" };
const DITO: Record<Resposta, string> = { sim: "confirmada.", nao: "recusada.", desfazer: "resposta desfeita." };

type Modo = "pendentes" | "respondidas";
// `falhaEm` é UM só para as duas pontas (a pilha e o fim da /previsao têm pais diferentes, então mora num contexto):
// o alerta aparece onde a ação falhou, e um sucesso (ou resposta que pegou) em qualquer uma o limpa.
const ContextoFalha = createContext<[Modo | null, (m: Modo | null) => void]>([null, () => {}]);
export const useFalhaReceita = () => useContext(ContextoFalha);
// A lista de receitas que a PÁGINA /previsao leu (no horizonte que ela mostra): a pilha do topo a usa em vez de ler a previsão por conta própria,
// porque um segundo observador da chave padrão guardaria o dado do plano antigo enquanto a página está em 60 ou 90 dias (e ela o adotaria no downgrade).
const ContextoLista = createContext<[Previsao["receitas_do_banco"] | null, (l: Previsao["receitas_do_banco"] | null) => void]>([null, () => {}]);
export const useListaDaPagina = () => useContext(ContextoLista);
export function ReceitasProvider({ children }: { children: ReactNode }) {
  return <ContextoFalha.Provider value={useState<Modo | null>(null)}><ContextoLista.Provider value={useState<Previsao["receitas_do_banco"] | null>(null)}>{children}</ContextoLista.Provider></ContextoFalha.Provider>;
}

export function useReceitas(modo: Modo) {
  const qc = useQueryClient();
  const [falhaEm, setFalhaEm] = useFalhaReceita();
  const falhou = falhaEm === modo;
  const setFalhou = (f: boolean) => setFalhaEm(f ? modo : null);
  // `n` muda a cada resposta: duas receitas de mesmo nome dão o mesmo texto, e sem DOM novo o leitor de tela não repete.
  const [dito, setDito] = useState({ t: "", n: 0 });
  const falar = (t: string) => setDito((d) => ({ t, n: d.n + 1 }));
  const raizRef = useRef<HTMLDivElement>(null);
  const tituloRef = useRef<HTMLParagraphElement>(null);
  const avisoRef = useRef<HTMLParagraphElement>(null);
  // O foco só vai para o resultado se o usuário não foi para outro lugar enquanto o POST demorava.
  // `preventScroll`: o botão clicado fica `disabled` e perde o foco, e quem rolou a página no meio não volta ao topo.
  const focar = (el: HTMLElement | null) => {
    const a = document.activeElement;
    if (!a || a === document.body || raizRef.current?.contains(a)) el?.focus({ preventScroll: true });
  };
  // A resposta do POST é a Previsão do MENOR horizonte; a tela pode estar em outro. Por isso
  // não se planta a resposta numa chave: toda `["previsao", …]` ativa é relida, e a mutação só
  // termina (e destrava os botões) depois da releitura. O GET também replanta o cookie de CSRF.
  // `networkMode: "always"`: sem rede o fetch falha e cai no onError, em vez de congelar a mutação.
  // `tituloRef` só existe nas respondidas: na pilha, quem leva o foco ao aviso que subiu é a PilhaAvisos (o visível saiu da lista).
  const m = useMutation({
    mutationKey: CHAVE,
    networkMode: "always",
    mutationFn: ({ r, resposta }: { r: ReceitaDoBanco; resposta: Resposta }) => apiPost("/previsao/receita", { chave: r.chave, resposta }),
    onSuccess: async (_, { r, resposta }) => {
      setFalhou(false); falar(`${r.nome}: ${DITO[resposta]}`);
      await qc.invalidateQueries({ queryKey: ["previsao"] });
      focar(tituloRef.current);
    },
    // A resposta pode ter se perdido com a marca já gravada: a releitura diz se pegou.
    // Releitura que também falha deixa a lista de antes do POST, onde a ação não aparece aplicada.
    onError: async (_, { r, resposta }) => {
      await qc.invalidateQueries({ queryKey: ["previsao"] });
      // Chave fora da lista relida NÃO conta como gravada: pode ser banco fora do ar ou cadeia que deixou de ser detectada.
      const pegou = qc.getQueriesData<Previsao>({ queryKey: ["previsao"] })
        .some(([, d]) => d?.receitas_do_banco?.some((x) => x.chave === r.chave && x.status === STATUS[resposta]));
      // O aviso vazio é `display: none` e não recebe foco: renderiza antes de focar.
      flushSync(() => { setFalhou(!pegou); falar(pegou ? `${r.nome}: ${DITO[resposta]}` : ""); });
      focar(pegou ? tituloRef.current : avisoRef.current);
    },
  });
  // `m.isPending` só muda no re-render: dois cliques no mesmo tique mandariam dois POST.
  const ocupado = () => qc.isMutating({ mutationKey: CHAVE }) > 0;
  const pendente = useIsMutating({ mutationKey: CHAVE }) > 0;
  return { m, falhou, dito, falar, raizRef, tituloRef, avisoRef, ocupado, pendente };
}
type Receitas = ReturnType<typeof useReceitas>;

// O alerta de falha (fora de qualquer pilha) e a região viva do anúncio.
export function FeedbackReceita({ r }: { r: Receitas }) {
  return (
    <>
      <p ref={r.avisoRef} tabIndex={-1} role="alert" className="rec-foco rec-erro">{r.falhou && "Não deu para salvar. Tente de novo."}</p>
      <p className="sr-only" role="status"><span key={r.dito.n}>{r.dito.t}</span></p>
    </>
  );
}

const botao = (r: Receitas, x: ReceitaDoBanco, resposta: Resposta, rotulo: string, aria: string, cls: string) => (
  <button type="button" className={`btn ${cls}`} disabled={r.pendente} aria-label={`${aria}: ${x.nome}`}
    onClick={() => { if (!r.ocupado()) r.m.mutate({ r: x, resposta }); }}>{rotulo}</button>
);
const meta = (x: ReceitaDoBanco) => <>todo dia {x.dia}</>;

// Cada pendente vira um aviso da pilha (decisão, rosa); a respondida não: ela mora em `RespondidasDoBanco`.
export function itensDeReceita(lista: Previsao["receitas_do_banco"], r: Receitas): AvisoDaPilha[] {
  return lista.filter((x) => x.status === "pendente").map((x) => ({
    id: `receita:${x.chave}`, tom: "decisao", icone: "ph-wallet", titulo: "Receitas do extrato", ponto: `Receitas do extrato, ${x.nome}`,
    corpo: (
      <ul className="subs" aria-label="Receitas a confirmar">
        <li className="sub">
          <b className="sub-name rec-nome">{x.nome}</b>
          <span className="sub-amt num">{moneyText(x.valor) ?? "—"}</span>
          <p className="sub-meta">{meta(x)}. É sua receita fixa?</p>
          <div className="sub-acoes">
            {botao(r, x, "sim", "Sim", "Sim, é receita fixa", "btn-ghost")}
            {botao(r, x, "nao", "Não", "Não é receita fixa", "btn-quiet")}
          </div>
        </li>
      </ul>
    ),
  }));
}

export function RespondidasDoBanco({ lista }: { lista: Previsao["receitas_do_banco"] }) {
  const r = useReceitas("respondidas");
  const respondidas = lista.filter((x) => x.status !== "pendente");
  // A seção fica montada enquanto houver o que anunciar: o Desfazer da última respondida esvazia a lista,
  // e o aviso e o foco precisam de onde pousar. Sem itens e sem erro ela vira marcação sr-only: nada visível, só o anúncio e o alvo do foco.
  if (!respondidas.length && !r.dito.t && !r.falhou) return null;
  const visivel = respondidas.length > 0 || r.falhou;
  return (
    <div ref={r.raizRef} className={visivel ? "panel span-12" : "sr-only"}>
      <Frame id="previsao-receitas-respondidas" title="Receitas na previsão" real>
        <p ref={r.tituloRef} tabIndex={-1} className="rec-foco sr-only">{respondidas.length ? "Receitas respondidas." : "Nenhuma receita respondida."}</p>
        <FeedbackReceita r={r} />
        {respondidas.length > 0 && (
          <ul className="subs" aria-label="Receitas respondidas">
            {respondidas.map((x) => (
              <li key={x.chave} className="sub">
                <b className="sub-name rec-nome">{x.nome}</b>
                <span className="sub-amt num">{moneyText(x.valor) ?? "—"}</span>
                <p className="sub-meta"><span className={`selo rec-${x.status}`}>{CHIP[x.status as keyof typeof CHIP]}</span> {meta(x)}</p>
                <div className="sub-acoes">{botao(r, x, "desfazer", "Desfazer", "Desfazer resposta", "btn-ghost")}</div>
              </li>
            ))}
          </ul>
        )}
      </Frame>
    </div>
  );
}
