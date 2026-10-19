// O corpo do aviso "Dados do banco desatualizados": o texto e a ação que de fato resolve (atualizar, reconectar ou ver os bancos). Porto de `app/src/features/painel/corpoAvisoBanco.tsx`.
// Sem a leitura do snapshot (lendo, falhou, conta trocada) fica o corpo do PR 1: o texto padrão e "Ver bancos conectados".
// "Atualizar agora" e "Atualizando…" são UM nó, na mesma posição: `aria-disabled` e não `disabled`, que tiraria o foco e o mandaria para o <body> (precedente: BoardControls.tsx).
import { href } from "../router";
import { ATUALIZANDO_SOZINHO, FALHA, type AcaoBanco, type EstadoAtualizar } from "../lib/atualizarBanco";
import { OF } from "./guia-falas";

export function CorpoAvisoBanco({ padrao, acao, maquina, atualizar }: { padrao: string; acao: AcaoBanco | null; maquina: EstadoAtualizar; atualizar: () => void }) {
  const texto = (t: string) => <p className="pilha-texto">{t}</p>;
  const ver = <a className="btn btn-ghost" href={href("/conta/bancos")}>Ver bancos conectados</a>;
  if (maquina.fase === "acompanhando") return texto(ATUALIZANDO_SOZINHO);
  if (maquina.fase === "resultado") return <>{texto(maquina.texto)}{ver}</>;
  const enviando = maquina.fase === "enviando";
  if (enviando || acao?.tipo === "atualizar") {
    return (
      <>
        {texto(padrao)}
        {!enviando && maquina.falha && texto(FALHA)}
        <button type="button" className="btn btn-ghost" aria-disabled={enviando ? "true" : undefined} onClick={() => { if (!enviando) atualizar(); }}>{enviando ? "Atualizando…" : "Atualizar agora"}</button>
      </>
    );
  }
  if (acao?.tipo === "reconectar") return <>{texto(padrao)}<a className="btn btn-ghost" href={OF}>{`Reconectar ${acao.banco}`}</a></>;
  return <>{acao?.tipo === "ver" && acao.texto ? texto(acao.texto) : texto(padrao)}{ver}</>;
}
