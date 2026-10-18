import { Acao } from "./base";
import { ATUALIZANDO_SOZINHO, FALHA } from "./atualizarBanco";
import { useNavegacaoPainel } from "./navegacao";
import { TextoPainel as Texto } from "./tipografia";
import { useAtualizarBanco } from "./usarAtualizarBanco";

/**
 * O corpo do aviso "Dados do banco desatualizados": o texto e a ação que de fato resolve (atualizar, reconectar ou ver os bancos).
 * "Atualizando sozinho" só com a máquina em `acompanhando` (há uma rodada lendo de fato); classificação (a0) sozinha cai no botão de sempre.
 * `bancos` = quem está velho, calculado por quem monta o aviso. Sem a leitura do snapshot (lendo ou falhou) fica o botão de sempre, "Ver bancos conectados".
 */
export function CorpoAvisoBanco({ bancos }: { bancos: string[] }) {
  const { navegar } = useNavegacaoPainel();
  const { acao, maquina, atualizar } = useAtualizarBanco();
  const padrao = <Texto tom="inkMuted">{`Os dados de ${bancos.length ? bancos.join(", ") : "um banco conectado"} precisam ser atualizados. Alguns valores podem estar incompletos.`}</Texto>;
  const ver = <Acao rotulo="Ver bancos conectados" onPress={() => navegar("/conexoes")} />;
  if (maquina.fase === "acompanhando") return <Texto tom="inkMuted">{ATUALIZANDO_SOZINHO}</Texto>;
  if (maquina.fase === "resultado") return <><Texto tom="inkMuted">{maquina.texto}</Texto>{ver}</>;
  if (maquina.fase === "enviando") return <>{padrao}<Acao rotulo="Atualizando…" desativado onPress={() => {}} /></>;
  if (acao?.tipo === "atualizar") return <>{padrao}{maquina.falha && <Texto>{FALHA}</Texto>}<Acao rotulo="Atualizar agora" onPress={atualizar} /></>;
  if (acao?.tipo === "reconectar") return <>{padrao}<Acao rotulo={`Reconectar ${acao.banco}`} onPress={() => navegar({ pathname: "/autorizando", params: { itemId: acao.itemId } })} /></>;
  return <>{acao?.tipo === "ver" && acao.texto ? <Texto tom="inkMuted">{acao.texto}</Texto> : padrao}{ver}</>;
}
