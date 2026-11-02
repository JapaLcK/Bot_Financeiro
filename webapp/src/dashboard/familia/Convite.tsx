import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ErroApi } from "../lib/v2";
import { familiaQuery, gerarConvite, revogarConvite, validade, type Convite as DadosConvite } from "./api";
import { focarTitulo, type useEscrita } from "./escrita";

// Convite: gera o link, mostra com a validade, copia/compartilha e revoga. O token só vive neste estado (nunca em storage, URL ou cache):
// sair da tela o perde, e o convite segue valendo até expirar (o titular não tem como listá-lo de novo).
export function Convite({ cheio, escrita }: { cheio: boolean; escrita: ReturnType<typeof useEscrita> }) {
  const { ocupado, rodar } = escrita;
  const qc = useQueryClient();
  const [convite, setConvite] = useState<DadosConvite | null>(null);
  const [aviso, setAviso] = useState("");
  const link = useRef<HTMLParagraphElement>(null);
  const tinha = useRef(false);
  useEffect(() => { // o botão clicado saiu da tela: gerar leva ao link novo; revogar, ao título
    if (convite) link.current?.focus(); else if (tinha.current) focarTitulo();
    tinha.current = !!convite;
  }, [convite]);

  const gerar = () => { setAviso(""); return rodar(async () => setConvite(await gerarConvite())); };
  const revogar = async () => {
    if (!convite) return;
    setAviso("");
    // 404 = o convite já não existe (aceito ou vencido): a meta (link fora de uso) está cumprida. Solta o link e relê a família.
    const ok = await rodar(() => revogarConvite(convite.token).catch((e) => {
      if (!(e instanceof ErroApi && e.status === 404)) throw e;
      qc.invalidateQueries({ queryKey: familiaQuery.queryKey });
    }));
    if (ok) setConvite(null);
  };
  const copiar = () => {
    if (!convite) return;
    Promise.resolve().then(() => navigator.clipboard.writeText(convite.link)).then(() => setAviso("Link copiado"), () => setAviso("Não foi possível copiar"));
  };
  const compartilhar = () => { if (convite) navigator.share({ url: convite.link }).catch(() => {}); }; // cancelar o painel rejeita: não é erro

  return <div className="fam-convite">
    {!convite && <button type="button" className="btn btn-primary" disabled={cheio} aria-disabled={ocupado || undefined} onClick={gerar}>Gerar link de convite</button>}
    {!convite && cheio && <p className="faint">Todos os lugares estão ocupados.</p>}
    {convite && <>
      <p ref={link} tabIndex={-1} className="fam-link" data-testid="fam-link">{convite.link}</p>
      <p className="faint">Vale até {validade(convite.expira_em)}.</p>
      <div className="fam-acoes">
        <button type="button" className="btn btn-primary" onClick={copiar}><i className="ph ph-clipboard-text" aria-hidden="true" />Copiar</button>
        {typeof navigator.share === "function" && <button type="button" className="btn btn-ghost" onClick={compartilhar}>Compartilhar</button>}
        <button type="button" className="btn btn-ghost" aria-disabled={ocupado || undefined} onClick={revogar}>Revogar</button>
      </div>
    </>}
    <p role="status" className="faint">{aviso}</p>
  </div>;
}
