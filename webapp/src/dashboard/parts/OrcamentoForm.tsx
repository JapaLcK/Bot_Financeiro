// Editar os 6 percentuais e a renda do mês (widgets/OrcamentoDomestico.tsx). O servidor valida de
// novo e devolve a conta pronta; a soma ao vivo aqui é só para o botão (centésimos inteiros, sem float).
import { useId, useRef, useState, type FormEvent } from "react";
import { useIsMutating, useMutation, useQueryClient } from "@tanstack/react-query";
import type { OrcamentoDomestico, PotesIn, RendaIn } from "../lib/api-v2.gen";
import { normalizaValor } from "../lib/format.js";
import { ErroApi, apiPost, orcamentoQuery } from "../lib/v2";
import { CRIAR_SENHA } from "./Entrada";
import { NATIVE, useDialogo } from "./dialog";

export type Modo = "potes" | "renda";
export const CHAVE_ESCRITA = ["orcamento-domestico", "escrita"];
type Escrita = { potes: PotesIn } | { renda: RendaIn };
type Falha = { geral: string; acao?: "planos" | "senha" };

const ERRO_POTES = "Confira os percentuais: cada pote vai de 0 a 100% e a soma fecha 100%. Seu rascunho foi mantido.";
const ERRO_RENDA = "Use um valor de 0 até 9 dígitos, com até 2 casas decimais, sem separador de milhares.";
const PERDIDA = "A resposta se perdeu. O orçamento pode ter sido salvo: confira os números antes de tentar de novo.";

// A resposta de erro em texto humano; nunca a `message` do envelope.
export function falhaDoOrcamento(e: unknown): Falha {
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { geral: PERDIDA };
  if (e.status === 422) {
    const campos = (e.details ?? []).flatMap((d) => d.loc);
    return { geral: campos.includes("valor") ? ERRO_RENDA : campos.includes("mes") ? "Esse mês ainda não chegou." : ERRO_POTES };
  }
  if (e.code === "pro_required") return { geral: "O Orçamento doméstico é do Plus e do Pro.", acao: "planos" };
  if (e.code === "password_required") return { geral: "Crie sua senha para salvar. Depois de criar, volte para o painel novo.", acao: "senha" };
  if (e.status === 403) return { geral: "Recarregue a página antes de salvar de novo." };
  return { geral: "Não foi possível salvar. Seu rascunho foi mantido." };
}

// Uma escrita por vez (a trava é a chave da mutação, lida por quem mostra os botões); o sucesso troca o cache pela resposta.
export function useEscrita(mes: string) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationKey: CHAVE_ESCRITA, retry: false, networkMode: "always",
    mutationFn: (a: Escrita) => "potes" in a ? apiPost("/orcamento-domestico/potes", a.potes) : apiPost("/orcamento-domestico/renda", a.renda),
    onSuccess: (r) => qc.setQueryData(orcamentoQuery(mes).queryKey, r),
    onError: () => qc.invalidateQueries({ queryKey: ["orcamento-domestico"] }), // resposta perdida: relê o que o servidor tem
  });
  return { mutate: m.mutate, mutateAsync: m.mutateAsync, isError: m.isError, erro: m.isError ? falhaDoOrcamento(m.error).geral : "" };
}

const PCT = /^\d{1,3}(\.\d{1,2})?$/;
// "12,5" -> 1250 centésimos; texto inválido ou fora de 0–100 -> null.
const centesimos = (txt: string) => {
  const n = normalizaValor(txt);
  if (!PCT.test(n)) return null;
  const [i, f = ""] = n.split(".");
  const c = Number(i) * 100 + Number(f.padEnd(2, "0"));
  return c <= 10000 ? c : null;
};
export const mostra = (pct: string) => pct.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "").replace(".", ",");
const brPct = (c: number) => `${Math.floor(c / 100)}${c % 100 ? "," + String(c % 100).padStart(2, "0").replace(/0$/, "") : ""}%`;

export function OrcamentoForm({ modo, mes, d, fechar }: { modo: Modo; mes: string; d: OrcamentoDomestico; fechar: () => void }) {
  const id = useId();
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar);
  const escrita = useEscrita(mes);
  const pendente = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`
  const [pcts, setPcts] = useState(() => d.potes.map((p) => mostra(p.pct)));
  const [renda, setRenda] = useState(() => d.renda.valor.replace(".", ","));
  const [falha, setFalha] = useState<Falha>({ geral: "" });

  const cs = pcts.map(centesimos);
  const soma = cs.reduce<number>((a, c) => a + (c ?? 0), 0);
  const potesOk = cs.every((c) => c !== null) && soma === 10000;
  const rendaNorm = normalizaValor(renda);
  const rendaOk = /^\d{1,9}(\.\d{1,2})?$/.test(rendaNorm); // zero é renda válida (≠ valorInvalido, que recusa 0)

  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current || pendente) return;
    if (modo === "renda" && !rendaOk) { setFalha({ geral: ERRO_RENDA }); return; }
    if (modo === "potes" && !potesOk) { setFalha({ geral: ERRO_POTES }); return; }
    voando.current = true;
    setFalha({ geral: "" });
    try {
      await escrita.mutateAsync(modo === "renda" ? { renda: { mes, valor: rendaNorm } }
        : { potes: { mes, potes: d.potes.map((p, i) => ({ chave: p.chave, pct: normalizaValor(pcts[i]) })) } });
      if (dlg.current?.isConnected) fecharDialog();
    } catch (err) { setFalha(falhaDoOrcamento(err)); }
    finally { voando.current = false; }
  };

  const resto = 10000 - soma;
  return <>
    <dialog {...props} className={`lanc-form orc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{modo === "renda" ? "Editar renda do mês" : "Editar percentuais"}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        <div className="lanc-campos">
          {modo === "renda" ? (
            <label>Renda do mês (R$)
              <input className="field" name="valor" type="text" inputMode="decimal" value={renda} disabled={pendente} onChange={(e) => setRenda(e.target.value)} aria-invalid={falha.geral === ERRO_RENDA} />
            </label>
          ) : <>
            {d.potes.map((p, i) => (
              <label key={p.chave}>{p.nome} (%)
                <input className="field" name={p.chave} type="text" inputMode="decimal" value={pcts[i]} disabled={pendente}
                  onChange={(e) => setPcts((x) => x.map((v, j) => j === i ? e.target.value : v))} aria-invalid={cs[i] === null} />
                {cs[i] === null && <span className="warn">Use de 0 a 100, com até 2 casas.</span>}
              </label>
            ))}
            <p role="status" className={`orc-soma${potesOk ? "" : " warn"}`}>
              Soma: <b className="num">{brPct(soma)}</b>{resto > 0 ? ` · faltam ${brPct(resto)}` : resto < 0 ? ` · passou ${brPct(-resto)}` : " · fecha 100%"}
            </p>
          </>}
        </div>
        {falha.geral && <p role="alert">{falha.geral}</p>}
        {falha.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        {falha.acao === "senha" && <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente || (modo === "potes" && !potesOk)}>{pendente ? "Salvando…" : "Salvar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
