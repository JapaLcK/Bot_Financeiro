import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErroApi, contaQuery } from "../lib/v2";
import { AVISO_PRO, familiaQuery, falhaFamilia, type Falha } from "./api";

// Uma escrita por vez: a trava é uma ref (dois cliques no mesmo tick não passam pelo estado). Falha vira texto; 404/409 (o estado mudou) relê a família.
export function useEscrita() {
  const qc = useQueryClient();
  const voando = useRef(false);
  const [ocupado, setOcupado] = useState(false);
  const [falha, setFalha] = useState<Falha | null>(null);
  const rodar = async (f: () => Promise<unknown>): Promise<boolean> => {
    if (voando.current) return false;
    voando.current = true;
    setOcupado(true);
    setFalha(null);
    try { await f(); return true; }
    catch (e) {
      setFalha(falhaFamilia(e));
      if (e instanceof ErroApi && (e.status === 404 || e.status === 409)) qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
      return false;
    } finally { voando.current = false; setOcupado(false); }
  };
  return { ocupado, falha, rodar, limpar: () => setFalha(null) };
}

// 402 de plano vira um card de upgrade (inline); `membro`: quem não pode assinar vê que a família está pausada, sem compra. No app iOS o CTA some (3.1.1, como o legado: auth-refresh.js).
export const FalhaFamilia = ({ falha, membro = false }: { falha: Falha | null; membro?: boolean }) => {
  if (!falha) return null;
  if (!falha.plano) return <p role="alert" className="warn fam-falha">{falha.texto}</p>;
  const a = membro ? AVISO_PRO.membro : AVISO_PRO.titular;
  const noApp = (window as { PB_IN_APP?: boolean }).PB_IN_APP;
  return (
    <div role="alert" className="fam-upgrade">
      <span className="fam-up-ico" aria-hidden="true"><i className="ph ph-users-three" /></span>
      <div className="fam-up-corpo">
        <p className="fam-up-tit">{a.titulo}</p>
        <p className="fam-up-txt">{a.texto}</p>
        {!membro && !noApp && <a className="btn btn-primary" href="/precos">Ver o Pro <i className="ph ph-arrow-right" aria-hidden="true" /></a>}
      </div>
    </div>
  );
};

// O perfil (de onde vem o uid) falhou: sem ele as leituras de membro nem começam. Refazer o perfil destrava as duas telas.
export const PerfilFalhou = ({ tentar }: { tentar: () => void }) => (
  <div className="empty" role="alert">
    <p>Não foi possível carregar.</p>
    <button type="button" className="btn btn-ghost retry" onClick={tentar}>Tentar de novo</button>
  </div>
);

// O elemento que tinha o foco saiu da tela (botão que virou outra coisa, linha removida, diálogo que acabou de fechar): o foco cairia no body. Vai ao título.
export const focarTitulo = () => {
  const a = document.activeElement;
  if (!a || a === document.body || !a.isConnected || a.closest("dialog:not([open])")) document.getElementById("page-title")?.focus();
};

// De quem é a tela (Família e Casa): o uid do perfil. O `key` muda quando o uid muda: trocar de conta com a página aberta remonta a tela e limpa o que o resetQueries (main.tsx) não alcança
// (o erro de ação, o diálogo aberto, o rascunho). O 1º uid que chega não conta como troca. O perfil não relê ao voltar à aba (contaQuery); aqui sim, porque é ele que diz de quem é a tela.
// `perfilFalhou`: sem uid as leituras nem começam, e a tela mostra o erro com este "Tentar de novo".
export function useDonoDaTela() {
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true });
  const uid = conta.data?.user_id;
  const perfilFalhou = uid === undefined && conta.isError ? () => void conta.refetch() : undefined;
  const visto = useRef<number | undefined>(undefined);
  const troca = useRef(0);
  if (uid !== undefined) { if (visto.current !== undefined && uid !== visto.current) troca.current++; visto.current = uid; }
  return { uid, perfilFalhou, chave: troca.current };
}
