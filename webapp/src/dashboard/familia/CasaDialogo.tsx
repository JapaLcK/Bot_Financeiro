import { createContext, useContext, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NATIVE, useDialogo } from "../parts/dialog";
import { href } from "../router";
import { ErroApi } from "../lib/v2";
import { casaQuery, deveReler, falhaCasa, type Casa, type FalhaCasa, type Tipo } from "./casa";

/** O aviso da PÁGINA (Casa.tsx o liga ao `FalhaFamilia`): o item que sumiu por baixo do diálogo fecha o diálogo e avisa aqui, o mesmo lugar do erro do Apagar. */
export const AvisoCasa = createContext<(f: FalhaCasa) => void>(() => {});

// O diálogo das escritas da casa (meta, orçamento, movimento): o molde de parts/OrcamentoCategoriaForm.tsx. Em qualquer erro o rascunho fica; a escrita em voo trava o fechar.
// Sucesso: a Casa que o servidor devolve entra no cache (sem 2º GET). Rascunho = estado do formulário filho; `recarregar` (409 edit_conflict) descarta o rascunho: fecha e relê.
export function CasaDialogo({ uid, tipo, titulo, rotulo, validar, enviar, fechar, apagar, filhos }: {
  uid: number; tipo: Tipo; titulo: string; rotulo: string;
  validar: () => FalhaCasa | null; enviar: () => Promise<Casa>; fechar: () => void; apagar?: () => void;
  filhos: (x: { falha: FalhaCasa | null; pendente: boolean }) => ReactNode;
}) {
  const id = useId();
  const qc = useQueryClient();
  const chave = casaQuery(uid).queryKey;
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `pendente`
  const [pendente, setPendente] = useState(false);
  const [falha, setFalha] = useState<FalhaCasa | null>(null);
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const reler = () => qc.invalidateQueries({ queryKey: chave });
  const avisar = useContext(AvisoCasa);

  const ir = async (e: FormEvent) => {
    e.preventDefault();
    if (voando.current) return;
    const local = validar();
    if (local) { setFalha(local); return; }
    voando.current = true;
    setPendente(true);
    setFalha(null);
    try {
      const casa = await enviar();
      await qc.cancelQueries({ queryKey: chave }); // uma leitura que saiu antes da escrita não pode voltar por cima dela
      qc.setQueryData(chave, casa);
      voando.current = false;
      if (dlg.current?.isConnected) fecharDialog();
    } catch (err) {
      if (err instanceof ErroApi && err.status === 404) { // o item (ou a família) sumiu por baixo do diálogo: nada a salvar nem a apagar, então ele fecha e a tela avisa
        voando.current = false;
        fecharDialog();
        avisar(falhaCasa(err, tipo));
      } else setFalha(falhaCasa(err, tipo));
      if (deveReler(err)) void reler();
    } finally { voando.current = false; setPendente(false); }
  };
  const recarregar = () => { fecharDialog(); void reler(); };

  return <>
    <dialog {...props} className={`lanc-form orc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <form onSubmit={ir} noValidate>
        <header><h2 id={`${id}-t`}>{titulo}</h2><button className="btn btn-quiet" type="button" onClick={fecharDialog} aria-label="Fechar">Fechar</button></header>
        <div className="lanc-campos">{filhos({ falha, pendente })}</div>
        {falha && <p role="alert">{falha.texto}</p>}
        {falha?.plano && <a className="btn btn-ghost" href={href("/conta/cobranca")}>Ver cobrança</a>}
        {falha?.recarregar && <button type="button" className="btn btn-ghost" onClick={recarregar}>Recarregar</button>}
        <footer>
          {apagar && <button className="btn btn-quiet" type="button" disabled={pendente} onClick={apagar}>Apagar</button>}
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Cancelar</button>
          <button className="btn btn-primary" type="submit" disabled={pendente}>{pendente ? "Salvando…" : rotulo}</button>
        </footer>
      </form>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}

// O campo de dinheiro dos três diálogos: texto com vírgula (nada de type=number), `aria-invalid` quando o erro é dele.
export const CampoTexto = ({ rotulo, valor, mudou, pendente, invalido, modo, max, nome }: { rotulo: string; valor: string; mudou: (v: string) => void; pendente: boolean; invalido: boolean; modo?: "decimal"; max?: number; nome: string }) => (
  <label>{rotulo}
    <input className="field" name={nome} type="text" inputMode={modo} maxLength={max} value={valor} disabled={pendente} onChange={(e) => mudou(e.target.value)} aria-invalid={invalido || undefined} />
  </label>
);
