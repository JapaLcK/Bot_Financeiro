// "+ Gasto fixo" da página /orcamento: cadastra um gasto fixo mensal (POST /gastos-fixos). Só débito em conta:
// o /api/v2 ainda não lista os cartões da pessoa. O servidor valida de novo; em qualquer erro o rascunho fica.
import { useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ERRO_VALOR, normalizaValor, valorInvalido } from "../lib/format.js";
import { ErroApi, apiPost, categoriasQuery } from "../lib/v2";
import { CRIAR_SENHA } from "./Entrada";
import { NATIVE, useDialogo } from "./dialog";

type Falha = { geral: string; acao?: "planos" | "senha" };
const ERRO_NOME = "Dê um nome de até 80 caracteres.";
const ERRO_DIA = "O dia do vencimento vai de 1 a 31.";
const ERRO_CAT = "Escolha uma das suas categorias.";
const PERDIDA = "A resposta se perdeu. O gasto fixo pode ter sido cadastrado: confira a Previsão antes de tentar de novo.";
const TEXTO: Record<string, string> = { nome: ERRO_NOME, valor: ERRO_VALOR, dia: ERRO_DIA, categoria: ERRO_CAT };

export function falhaDoGastoFixo(e: unknown): Falha {
  if (!(e instanceof ErroApi) || e.status === 0 || e.status >= 500 || e.status < 400) return { geral: PERDIDA };
  if (e.status === 422) {
    const campo = (e.details ?? []).flatMap((d) => d.loc).find((c) => c in TEXTO);
    return { geral: (campo && TEXTO[campo]) || "Confira os dados. Seu rascunho foi mantido." };
  }
  if (e.code === "pro_required") return { geral: "Gasto fixo é do Essencial para cima.", acao: "planos" };
  if (e.code === "password_required") return { geral: "Crie sua senha para salvar. Depois de criar, volte para o painel novo.", acao: "senha" };
  if (e.status === 403) return { geral: "Recarregue a página antes de salvar de novo." };
  return { geral: "Não foi possível cadastrar. Seu rascunho foi mantido." };
}

export function GastoFixoForm({ feito, fechar }: { feito: () => void; fechar: () => void }) {
  const id = useId();
  const qc = useQueryClient();
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar);
  const cats = useQuery(categoriasQuery);
  const [nome, setNome] = useState("");
  const [valor, setValor] = useState("");
  const [dia, setDia] = useState("");
  const [cat, setCat] = useState("");
  const [falha, setFalha] = useState<Falha>({ geral: "" });
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `isPending`
  const m = useMutation({
    retry: false, networkMode: "always",
    mutationFn: (c: Parameters<typeof apiPost<"/gastos-fixos">>[1]) => apiPost("/gastos-fixos", c),
    onSettled: () => qc.invalidateQueries({ queryKey: ["previsao"] }), // entra na Previsão; no erro perdido, relê o que o servidor tem
  });

  const escolhida = cat || cats.data?.categorias[0]?.nome || "";
  const norm = normalizaValor(valor);
  const diaN = /^\d{1,2}$/.test(dia.trim()) ? Number(dia) : 0;
  const nomeOk = nome.trim().length > 0 && nome.trim().length <= 80;
  const enviar = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current || m.isPending) return;
    if (!nomeOk) { setFalha({ geral: ERRO_NOME }); return; }
    if (valorInvalido(norm)) { setFalha({ geral: ERRO_VALOR }); return; }
    if (diaN < 1 || diaN > 31) { setFalha({ geral: ERRO_DIA }); return; }
    if (!escolhida) { setFalha({ geral: ERRO_CAT }); return; }
    voando.current = true;
    setFalha({ geral: "" });
    try {
      await m.mutateAsync({ nome: nome.trim(), valor: norm, dia: diaN, categoria: escolhida, forma: "conta" });
      feito();
      if (dlg.current?.isConnected) fecharDialog();
    } catch (err) { setFalha(falhaDoGastoFixo(err)); }
    finally { voando.current = false; }
  };

  const ocupado = m.isPending;
  return <>
    <dialog {...props} className={`lanc-form orc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={enviar} noValidate>
        <header><h2 id={`${id}-t`}>Novo gasto fixo</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        <p className="faint">Todo mês, no débito da conta. Ele entra na Previsão e só conta no pote quando o pagamento vira lançamento.</p>
        <div className="lanc-campos">
          <label>Nome
            <input className="field" name="nome" type="text" maxLength={80} value={nome} disabled={ocupado} onChange={(e) => setNome(e.target.value)} aria-invalid={falha.geral === ERRO_NOME} />
          </label>
          <label>Valor (R$)
            <input className="field" name="valor" type="text" inputMode="decimal" value={valor} disabled={ocupado} onChange={(e) => setValor(e.target.value)} aria-invalid={falha.geral === ERRO_VALOR} />
          </label>
          <label>Dia do vencimento
            <input className="field" name="dia" type="text" inputMode="numeric" maxLength={2} value={dia} disabled={ocupado} onChange={(e) => setDia(e.target.value)} aria-invalid={falha.geral === ERRO_DIA} />
          </label>
          {cats.isError ? (
            <div className="empty" role="alert">
              <p>Não foi possível carregar as categorias.</p>
              <button type="button" className="btn btn-ghost retry" onClick={() => cats.refetch()}>Tentar de novo</button>
            </div>
          ) : <label>Categoria
            <select className="field" name="categoria" disabled={ocupado || !cats.data} value={escolhida} onChange={(e) => setCat(e.target.value)}>
              {(cats.data?.categorias ?? []).map((c) => <option key={c.chave} value={c.nome}>{c.nome}</option>)}
            </select>
          </label>}
        </div>
        {falha.geral && <p role="alert">{falha.geral}</p>}
        {falha.acao === "planos" && <a className="btn btn-ghost" href="/precos">Ver planos</a>}
        {falha.acao === "senha" && <a className="btn btn-primary" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>}
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={ocupado || !escolhida}>{ocupado ? "Salvando…" : "Cadastrar"}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
