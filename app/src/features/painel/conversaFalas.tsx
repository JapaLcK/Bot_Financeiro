import type { ReactNode } from "react";
import { ActivityIndicator, Image, View } from "react-native";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { Bloco } from "./base";
import { mesAtual, type Widget } from "./catalogo";
import { raio, texto as escalas } from "@/ui/tokens";
import { ChipPainel, TextoPainel as Texto } from "./tipografia";
import { usePainel } from "./provider";
import { sugestoes, widgetDoTopico, WIDGETS_DO_MES, type Pergunta } from "./perguntas";
import type { Turno } from "./conversaEstado";
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
/** Um turno: pergunta à direita, fala do Piggy à esquerda e, se o chip tinha assunto e a resposta veio, o widget real abaixo. */
export function TurnoChat({ turno: t, onLayout, abrir }: { turno: Turno; onLayout?: (y: number) => void; abrir?: (w: Widget) => void }) {
  const p = usePainel(); const widget = t.assunto && widgetDoTopico(t.assunto);
  const card = !p.oculto && t.resposta !== undefined && widget && (!WIDGETS_DO_MES.includes(widget) || p.mes === mesAtual()) ? widget : null;
  return <View onLayout={(e) => onLayout?.(e.nativeEvent.layout.y)} style={{ gap: 12 }}>
    {p.oculto ? <Texto tom="inkMuted">{OCULTA}</Texto> : <>
      <BolhaUsuario texto={t.pergunta} />
      {t.erro || t.resposta !== undefined ? <FalaPiggy>{t.erro ? <Texto tom="warning">{t.erro}</Texto> : <ComNegrito texto={t.resposta!} />}{t.nota && <ComNegrito texto={`Cancelei *${t.nota}* porque você saiu do chat.`} />}{card && <Bloco id={card} abrir={abrir && (() => abrir(card))}><WidgetConteudo id={card} /></Bloco>}</FalaPiggy> : <Digitando />}
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
