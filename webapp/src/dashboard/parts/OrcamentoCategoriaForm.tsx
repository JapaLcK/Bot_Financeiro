// Criar, editar e apagar um orçamento por categoria (widgets/OrcamentosCategoria.tsx). O servidor valida de novo
// e devolve a lista pronta, que entra no cache. Em qualquer erro o rascunho fica; o texto nunca é a `message` do envelope.
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Orcamento, OrcamentosCategoria } from "../lib/api-v2.gen";
import { ERRO_VALOR, normalizaValor, valorInvalido } from "../lib/format.js";
import { ErroApi, apiPost, categoriasQuery, orcamentosCategoriaQuery } from "../lib/v2";
import { CRIAR_SENHA } from "./Entrada";
import { NATIVE, useDialogo } from "./dialog";

export const CHAVE_ESCRITA = ["orcamentos-categoria", "escrita"];
type Escrita = { apagar: boolean; categoria: string; valor: string };
type Falha = { geral: string; acao?: "planos" | "senha" };

const ERRO_CATEGORIA = "Escolha uma das suas categorias.";
const PERDIDA = "A resposta se perdeu. O orçamento pode ter sido salvo: confira antes de tentar de novo.";

function falhaDoOrcamento(e: unknown, limite: number | null): Falha {
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { geral: PERDIDA };
  if (e.status === 422) {
    const campos = (e.details ?? []).flatMap((d) => d.loc);
    return { geral: campos.includes("valor") ? ERRO_VALOR : campos.includes("categoria") ? ERRO_CATEGORIA : "Não foi possível salvar. Seu rascunho foi mantido." };
  }
  if (e.code === "plan_limit") return { geral: limite === null ? "Você chegou ao limite de orçamentos do seu plano." : `Você chegou ao limite de ${limite} orçamentos do seu plano.`, acao: "planos" };
  if (e.code === "password_required") return { geral: "Crie sua senha para salvar. Depois de criar, volte para o painel novo.", acao: "senha" };
  if (e.status === 403) return { geral: "Recarregue a página antes de salvar de novo." };
  return { geral: "Não foi possível salvar. Seu rascunho foi mantido." };
}

export function OrcamentoCategoriaForm({ orcamento, orcamentos, limite, fechar }: {
  orcamento?: Orcamento; orcamentos: Orcamento[]; limite: number | null; fechar: () => void;
}) {
  const id = useId();
  const qc = useQueryClient();
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar);
  const novo = !orcamento;
  const cats = useQuery({ ...categoriasQuery, enabled: novo });
  const usadas = new Set(orcamentos.map((o) => o.chave));
  const livres = (cats.data?.categorias ?? []).filter((c) => !usadas.has(c.chave));
  const [cat, setCat] = useState("");
  const [valor, setValor] = useState(orcamento ? orcamento.teto.replace(".", ",") : "");
  const [apagar, setApagar] = useState(false);
  const [falha, setFalha] = useState<Falha>({ geral: "" });
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`
  const m = useMutation({
    mutationKey: CHAVE_ESCRITA, retry: false, networkMode: "always",
    mutationFn: (a: Escrita) => a.apagar ? apiPost("/orcamentos-categoria/apagar", { categoria: a.categoria }) : apiPost("/orcamentos-categoria", { categoria: a.categoria, valor: a.valor }),
    onSuccess: (r: OrcamentosCategoria) => qc.setQueryData(orcamentosCategoriaQuery.queryKey, r),
    onError: () => qc.invalidateQueries({ queryKey: ["orcamentos-categoria"] }), // resposta perdida: relê o que o servidor tem
  });
  const pendente = useIsMutating({ mutationKey: CHAVE_ESCRITA }) > 0;
  const naTela = useRef(false); // o botão clicado (Apagar, Voltar) sai do DOM: o foco vai para o controle seguinte
  useEffect(() => {
    if (apagar === naTela.current) return;
    naTela.current = apagar;
    dlg.current?.querySelector<HTMLElement>(apagar ? "button[type=submit]" : "[data-apagar]")?.focus();
  }, [apagar]);

  const escolhida = orcamento?.categoria ?? (cat || livres[0]?.nome || "");
  const norm = normalizaValor(valor);
  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current || pendente || !escolhida) return;
    if (!apagar && valorInvalido(norm)) { setFalha({ geral: ERRO_VALOR }); return; }
    voando.current = true;
    setFalha({ geral: "" });
    try { await m.mutateAsync({ apagar, categoria: escolhida, valor: norm }); if (dlg.current?.isConnected) fecharDialog(); }
    catch (err) {
      if (apagar && err instanceof ErroApi && err.status === 404) { if (dlg.current?.isConnected) fecharDialog(); } // já foi: a invalidação já rodou
      else setFalha(falhaDoOrcamento(err, limite));
    }
    finally { voando.current = false; }
  };

  const titulo = novo ? "Novo orçamento" : apagar ? `Apagar o orçamento de ${orcamento.categoria}?` : "Editar orçamento";
  return <>
    <dialog {...props} className={`lanc-form orc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>{titulo}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        {apagar ? <p>Os seus lançamentos não mudam.</p> : <div className="lanc-campos">
          {novo ? (cats.isError ? (
            <div className="empty" role="alert">
              <p>Não foi possível carregar as categorias.</p>
              <button type="button" className="btn btn-ghost retry" onClick={() => cats.refetch()}>Tentar de novo</button>
            </div>
          ) : cats.data && !livres.length ? <p role="status">Todas as suas categorias já têm orçamento.</p>
            : <label>Categoria
              <select className="field" name="categoria" disabled={pendente || !cats.data} value={escolhida} onChange={(e) => setCat(e.target.value)}>
                {livres.map((c) => <option key={c.chave} value={c.nome}>{c.nome}</option>)}
              </select>
            </label>
          ) : <p className="orc-fixa"><span className="orc-rotulo">Categoria</span><b>{orcamento.categoria}</b></p>}
          <label>Teto do mês (R$)
            <input className="field" name="valor" type="text" inputMode="decimal" value={valor} disabled={pendente} onChange={(e) => setValor(e.target.value)} aria-invalid={falha.geral === ERRO_VALOR} />
          </label>
        </div>}
        {falha.geral && <p role="alert">{falha.geral}</p>}
        {falha.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        {falha.acao === "senha" && <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
        <footer>
          {!novo && !apagar && <button className="btn btn-quiet" type="button" data-apagar disabled={pendente} onClick={() => setApagar(true)}>Apagar</button>}
          {apagar && <button className="btn btn-ghost" type="button" onClick={() => setApagar(false)}>Voltar</button>}
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente || !escolhida}>{pendente ? "Salvando…" : apagar ? "Apagar" : "Salvar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
