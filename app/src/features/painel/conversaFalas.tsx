import type { ReactNode } from "react";
import { setStringAsync } from "expo-clipboard";
import { ActivityIndicator, Image, Pressable, View } from "react-native";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { useToast } from "@/ui/componentes/Toast";
import { sucesso } from "@/ui/haptics";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { Bloco } from "./base";
import { mesAtual, type Widget } from "./catalogo";
import { raio, texto as escalas } from "@/ui/tokens";
import { ChipPainel, TextoPainel as Texto } from "./tipografia";
import { usePainel } from "./provider";
import { sugestoes, widgetDoTopico, WIDGETS_DO_MES, type Pergunta } from "./perguntas";
import { useConversa, type Turno } from "./conversaEstado";
import { WidgetConteudo } from "./widgets";
export const OCULTA = "Mensagem oculta enquanto os valores estão privados.";
/** O servidor usa o negrito do WhatsApp (`*x*`): o conteúdo começa e termina com não-espaço, colado aos asteriscos; dígito colado não vale (`5*3*4` é conta). */
const NEGRITO = /(?<![0-9*])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![0-9*])/; // `**x**` fica como veio
export function ComNegrito({ texto }: { texto: string }) {
  return <Texto>{texto.split(NEGRITO).map((parte, i) => i % 2 ? <Texto key={i} style={{ fontFamily: escalas.secao.fontFamily }}>{parte}</Texto> : parte)}</Texto>;
}
export function BolhaUsuario({ texto }: { texto: string }) {
  const { cores } = useTema();
  return <View testID="bolha-usuario" style={{ alignSelf: "flex-end", maxWidth: "80%", backgroundColor: cores.surfaceControl, borderRadius: raio.bloco, borderBottomRightRadius: 4, paddingHorizontal: 14, paddingVertical: 10 }}><Texto>{texto}</Texto></View>;
}
export function FalaPiggy({ children }: { children: ReactNode }) {
  return <View testID="fala-piggy" style={{ alignSelf: "flex-start", maxWidth: "100%", gap: 6 }}><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Image source={SIMBOLO} style={{ width: 24, height: 24 }} accessibilityIgnoresInvertColors /><Texto testID="fala-piggy-nome" variante="legenda" tom="inkMuted">Piggy</Texto></View>{children}</View>;
}
export function Digitando() {
  return <FalaPiggy><View style={{ alignSelf: "flex-start" }}><ActivityIndicator accessibilityLabel="Piggy está respondendo" /></View></FalaPiggy>;
}
function BotaoAcao({ rotulo, icone, selecionado, onPress }: { rotulo: string; icone: NomeIcone; selecionado?: boolean; onPress: () => void }) {
  const { cores } = useTema(); const marcado = !!selecionado;
  return <Pressable accessibilityRole="button" accessibilityLabel={rotulo} accessibilityState={selecionado === undefined ? undefined : { selected: marcado }} onPress={onPress} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}>
    <View style={{ width: 32, height: 32, borderRadius: raio.pilula, alignItems: "center", justifyContent: "center", backgroundColor: marcado ? cores.brandSoft : undefined }}><Icone nome={icone} tamanho={20} tom={marcado ? "brandInk" : "inkMuted"} /></View>
  </Pressable>;
}
/** Copiar (sem os `*` do negrito do WhatsApp) e 👍👎 (só com `messageId`: resposta de erro/cota não tem nota). */
function Acoes({ turno: t }: { turno: Turno }) {
  const c = useConversa(); const toast = useToast();
  const copiar = async () => {
    try { await setStringAsync(t.resposta!.replace(new RegExp(NEGRITO.source, "g"), "$1")); sucesso(); toast.mostrar({ mensagem: "Copiado", tom: "sucesso" }); }
    catch { toast.mostrar({ mensagem: "Não conseguimos copiar.", tom: "erro" }); }
  };
  const nota = async (n: 1 | -1) => { if (!(await c.avaliar(t, n))) toast.mostrar({ mensagem: "Não conseguimos registrar sua avaliação.", tom: "erro" }); };
  return <View style={{ flexDirection: "row", marginLeft: -10 }}>
    <BotaoAcao rotulo="Copiar resposta" icone="Copy" onPress={() => void copiar()} />
    {t.messageId !== null && <><BotaoAcao rotulo="Gostei da resposta" icone="ThumbsUp" selecionado={t.feedback === 1} onPress={() => void nota(1)} /><BotaoAcao rotulo="Não gostei da resposta" icone="ThumbsDown" selecionado={t.feedback === -1} onPress={() => void nota(-1)} /></>}
  </View>;
}
/** Um turno: pergunta à direita, fala do Piggy à esquerda (texto parcial enquanto o stream corre) e, se o chip tinha assunto e a resposta veio, o widget real abaixo. */
export function TurnoChat({ turno: t, onLayout, abrir }: { turno: Turno; onLayout?: (y: number) => void; abrir?: (w: Widget) => void }) {
  const p = usePainel(); const widget = t.assunto && widgetDoTopico(t.assunto);
  const card = !p.oculto && t.resposta !== undefined && widget && (!WIDGETS_DO_MES.includes(widget) || p.mes === mesAtual()) ? widget : null;
  return <View onLayout={(e) => onLayout?.(e.nativeEvent.layout.y)} style={{ gap: 12 }}>
    {p.oculto ? <Texto tom="inkMuted">{OCULTA}</Texto> : <>
      <BolhaUsuario texto={t.pergunta} />
      {t.erro || t.resposta !== undefined ? <FalaPiggy>{t.erro ? <Texto tom="warning">{t.erro}</Texto> : <ComNegrito texto={t.resposta!} />}{t.nota && <ComNegrito texto={`Cancelei *${t.nota}* porque você saiu do chat.`} />}{card && <Bloco id={card} abrir={abrir && (() => abrir(card))}><WidgetConteudo id={card} /></Bloco>}{t.resposta !== undefined && <Acoes turno={t} />}</FalaPiggy>
        : t.interrompida ? <FalaPiggy>{!!t.parcial && <ComNegrito texto={t.parcial} />}<Texto variante="legenda" tom="inkMuted">Você parou esta resposta.</Texto></FalaPiggy>
        : t.parcial ? <FalaPiggy><ComNegrito texto={t.parcial} /></FalaPiggy> : <Digitando />}
    </>}
  </View>;
}
export function EstadoVazio({ desativado, escolher }: { desativado: boolean; escolher: (p: Pergunta) => void }) {
  const p = usePainel();
  return <View style={{ gap: 12 }}>
    <View style={{ alignItems: "flex-start", gap: 8 }}><Image source={SIMBOLO} style={{ width: 28, height: 28 }} accessibilityIgnoresInvertColors /><Texto>Oi, eu sou o Piggy. Pergunta o que quiser sobre o seu dinheiro, ou começa por uma destas:</Texto></View>
    <View style={{ alignItems: "flex-start" }}>{sugestoes(p.perfil).map((s) => <ChipPainel key={s.key} rotulo={s.ask} selecionado={false} desativado={desativado} onPress={() => escolher(s)} />)}</View>
  </View>;
}
