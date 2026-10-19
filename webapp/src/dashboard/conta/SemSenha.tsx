import { useId } from "react";
import { NATIVE, useDialogo } from "../parts/dialog";

// Conta só-Google (`/auth/me` diz sem senha): ativar/desativar/regerar o MFA pede uma senha que não existe. Nada é enviado (a
// /settings antiga faz o mesmo diálogo). `definir` leva ao botão "Definir senha" da própria página.
export function SemSenha({ fechar, definir }: { fechar: () => void; definir: () => void }) {
  const id = useId();
  const { fecharDialog, fundo, props } = useDialogo(fechar);
  return <>
    <dialog {...props} className={`lanc-form${NATIVE ? "" : " lanc-form-fb"}`} aria-labelledby={`${id}-t`}>
      <div className="cofre">
        <h2 id={`${id}-t`}>Defina uma senha primeiro</h2>
        <p>Sua conta ainda não tem senha, então nenhuma senha funciona aqui. Defina uma senha por e-mail e volte para concluir.</p>
        <p>Já definiu a senha em outra aba? Atualize a página.</p>
        <footer>
          <button className="btn btn-ghost" type="button" onClick={fecharDialog}>Agora não</button>
          <button className="btn btn-primary" type="button" onClick={() => { fecharDialog(); definir(); }}>Definir senha</button>
        </footer>
      </div>
    </dialog>
    {!NATIVE && <div className="lanc-scrim" aria-hidden="true" onClick={fundo} />}
  </>;
}
