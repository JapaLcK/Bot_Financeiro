import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ErroApi, contaQuery } from "../lib/v2";
import { href } from "../router";
import { familiaQuery, falhaFamilia, type Falha } from "./api";

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

export const FalhaFamilia = ({ falha }: { falha: Falha | null }) => falha && (
  <p role="alert" className="warn fam-falha">{falha.texto}{falha.plano && <> <a href={href("/conta/cobranca")}>Ver cobrança</a></>}</p>
);

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
