// O 4xx de uma página de ferramenta paga: convite do plano, senha por criar, ou sessão/erro com
// "Recarregar". `o` é o nome da ferramenta com artigo ("o orçamento"): entra nas duas frases.
import { ErroApi } from "../lib/v2";
import { ConvitePro } from "../widgets/Compromissos";
import { CRIAR_SENHA } from "./Entrada";

export function Bloqueio({ e, convite, o }: { e: ErroApi; convite: string; o: string }) {
  if (e.code === "pro_required") return <ConvitePro texto={convite} />;
  if (e.code === "password_required") {
    return (
      <div className="empty" role="alert">
        <p>Crie sua senha para ver {o}. Depois de criar, volte para o painel novo.</p>
        <a className="btn btn-ghost retry" href={CRIAR_SENHA.href}>{CRIAR_SENHA.texto}</a>
      </div>
    );
  }
  return (
    <div className="empty" role="alert">
      <p>{e.status === 401 ? "Sua sessão terminou." : `Não deu para abrir ${o}.`}</p>
      <button type="button" className="btn btn-ghost retry" onClick={() => location.reload()}>Recarregar</button>
    </div>
  );
}
