// Receitas que o servidor achou no extrato (`receitas_do_banco` da Previsão): só a que o usuário
// confirma entra na conta. Duas instâncias na página /previsao, mesma mutationKey (então `useIsMutating`
// trava as duas juntas): `modo="pendentes"` no topo (some quando não há o que perguntar) e
// `modo="respondidas"` no fim, com o Desfazer. Sem painel visível, a marcação sr-only fica montada
// para o anúncio e o foco do resultado. Sim/Não/Desfazer = POST /previsao/receita; sem atualização otimista:
// a lista na tela é sempre a última resposta do servidor. O nome é texto livre do pagador:
// só como texto do React, truncado no CSS.
import { useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useIsMutating, useMutation, useQueryClient } from "@tanstack/react-query";
import type { Previsao, ReceitaDoBanco, RespostaReceitaIn } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { apiPost } from "../lib/v2";
import { Frame } from "../parts/Frame";

const CHAVE = ["previsao", "receita"];
const CHIP = { confirmada: "Confirmada", negada: "Recusada" } as const;
type Resposta = RespostaReceitaIn["resposta"];
// O status que a lista passa a ter quando o POST pegou, e o que o leitor de tela ouve.
const STATUS: Record<Resposta, string> = { sim: "confirmada", nao: "negada", desfazer: "pendente" };
const DITO: Record<Resposta, string> = { sim: "confirmada.", nao: "recusada.", desfazer: "resposta desfeita." };

type Modo = "pendentes" | "respondidas";
// `falhaEm` mora no pai e é UM só para as duas instâncias: o alerta aparece onde a ação falhou, e um sucesso (ou resposta que pegou) em qualquer uma o limpa.
export function ReceitasDoBanco({ lista, modo, falhaEm, setFalhaEm }: { lista: Previsao["receitas_do_banco"]; modo: Modo; falhaEm: Modo | null; setFalhaEm: (m: Modo | null) => void }) {
  const qc = useQueryClient();
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
  const pendentes = lista.filter((r) => r.status === "pendente");
  const respondidas = lista.filter((r) => r.status !== "pendente");
  const itens = modo === "pendentes" ? pendentes : respondidas;
  // A caixa fica montada enquanto houver o que anunciar: o Sim da última pendente (ou o Desfazer da última respondida) esvazia a lista,
  // e o aviso e o foco precisam de onde pousar. Sem itens e sem erro ela vira marcação sr-only: nada visível, só o anúncio e o alvo do foco.
  if (!itens.length && !dito.t && !falhou) return null;
  const visivel = itens.length > 0 || falhou;

  const botao = (r: ReceitaDoBanco, resposta: Resposta, rotulo: string, aria: string, cls: string) => (
    <button type="button" className={`btn ${cls}`} disabled={pendente} aria-label={`${aria}: ${r.nome}`}
      onClick={() => { if (!ocupado()) m.mutate({ r, resposta }); }}>{rotulo}</button>
  );
  const meta = (r: ReceitaDoBanco) => <>todo dia {r.dia}</>;
  return (
    <div ref={raizRef} className={visivel ? "panel span-12" : "sr-only"}>
      <Frame id={modo === "pendentes" ? "previsao-receitas" : "previsao-receitas-respondidas"} title={modo === "pendentes" ? "Receitas do extrato" : "Receitas na previsão"} real>
        <p ref={tituloRef} tabIndex={-1} className={`rec-foco ${modo === "pendentes" ? "w-lede" : "sr-only"}`}>
          {modo === "pendentes"
            ? (pendentes.length ? "Entram na previsão só as que você confirmar." : "Nenhuma receita a confirmar.")
            : (respondidas.length ? "Receitas respondidas." : "Nenhuma receita respondida.")}
        </p>
        <p ref={avisoRef} tabIndex={-1} role="alert" className="rec-foco rec-erro">{falhou && "Não deu para salvar. Tente de novo."}</p>
        <p className="sr-only" role="status"><span key={dito.n}>{dito.t}</span></p>
        {modo === "pendentes" && pendentes.length > 0 && (
          <ul className="subs" aria-label="Receitas a confirmar">
            {pendentes.map((r) => (
              <li key={r.chave} className="sub">
                <b className="sub-name rec-nome">{r.nome}</b>
                <span className="sub-amt num">{moneyText(r.valor) ?? "—"}</span>
                <p className="sub-meta">{meta(r)}. É sua receita fixa?</p>
                <div className="sub-acoes">
                  {botao(r, "sim", "Sim", "Sim, é receita fixa", "btn-ghost")}
                  {botao(r, "nao", "Não", "Não é receita fixa", "btn-quiet")}
                </div>
              </li>
            ))}
          </ul>
        )}
        {modo === "respondidas" && respondidas.length > 0 && (
          <ul className="subs" aria-label="Receitas respondidas">
            {respondidas.map((r) => (
              <li key={r.chave} className="sub">
                <b className="sub-name rec-nome">{r.nome}</b>
                <span className="sub-amt num">{moneyText(r.valor) ?? "—"}</span>
                <p className="sub-meta"><span className={`selo rec-${r.status}`}>{CHIP[r.status as keyof typeof CHIP]}</span> {meta(r)}</p>
                <div className="sub-acoes">{botao(r, "desfazer", "Desfazer", "Desfazer resposta", "btn-ghost")}</div>
              </li>
            ))}
          </ul>
        )}
      </Frame>
    </div>
  );
}
