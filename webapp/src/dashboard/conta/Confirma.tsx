import { useEffect, useId, useRef, useState } from "react";
import { JANELA_DO_DUPLO_CLIQUE, NATIVE, useDialogo } from "../parts/dialog";

// O diálogo de confirmação. `agir` NUNCA lança (o pai guarda a falha); terminado, o diálogo sempre fecha.
// `pendente` é o texto do botão em voo; `cancelar`, o do botão que recusa.
export function Confirma({ titulo, texto, botao, pendente, cancelar = "Cancelar", agir, fechar }: { titulo: string; texto: string; botao: string; pendente: string; cancelar?: string; agir: () => Promise<void>; fechar: () => void }) {
  const id = useId();
  const voando = useRef(false); // síncrona: dois cliques/Enter no mesmo tick não passam por `emVoo`; também trava o fechar
  const [emVoo, setEmVoo] = useState(false);
  // O 2º clique do duplo clique que abriu o diálogo cai onde estava o botão de baixo, e ali pode estar o "Confirmar" (#1411). Na janela os
  // botões não recebem ponteiro: o clique atravessa e cai no `.cofre`, que não é fundo. Teclado e leitor de tela não passam por hit-test.
  const [cedo, setCedo] = useState(true);
  useEffect(() => { const t = setTimeout(() => setCedo(false), JANELA_DO_DUPLO_CLIQUE); return () => clearTimeout(t); }, []);
  const { dlg, fecharDialog, fundo, props } = useDialogo(fechar, () => voando.current);
  const ir = async () => {
    if (voando.current) return;
    voando.current = true;
    setEmVoo(true);
    await agir();
    voando.current = false; // o fechar está travado em voo
    if (dlg.current?.isConnected) fecharDialog();
  };
  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="cofre">
        <h2 id={`${id}-t`}>{titulo}</h2>
        <p>{texto}</p>
        <footer style={cedo ? { pointerEvents: "none" } : undefined}>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog} disabled={emVoo}>{cancelar}</button>
          <button className="btn btn-primary" type="button" onClick={ir} disabled={emVoo}>{emVoo ? pendente : botao}</button>
        </footer>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
