import { useEffect, useState, type ReactNode } from "react";
import { setStringAsync } from "expo-clipboard";
import { ActivityIndicator, Image, Pressable, View } from "react-native";
import Animated, { FadeIn } from "react-native-reanimated";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { useToast } from "@/ui/componentes/Toast";
import { Skeleton } from "@/ui/componentes/Skeleton";
import { sucesso } from "@/ui/haptics";
import { duracoes, useEntrada, useReduzirMovimento } from "@/ui/motion";
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
  const { cores } = useTema(); const entrada = useEntrada();
  return <Animated.View entering={entrada} testID="bolha-usuario" style={{ alignSelf: "flex-end", maxWidth: "80%", backgroundColor: cores.surfaceControl, borderRadius: raio.bloco, borderBottomRightRadius: 4, paddingHorizontal: 14, paddingVertical: 10 }}><Texto>{texto}</Texto></Animated.View>;
}
export function FalaPiggy({ children }: { children: ReactNode }) {
  const entrada = useEntrada();
  return <Animated.View entering={entrada} testID="fala-piggy" style={{ alignSelf: "flex-start", width: "100%", gap: 6 }}><View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Image source={SIMBOLO} style={{ width: 24, height: 24 }} accessibilityIgnoresInvertColors /><Texto testID="fala-piggy-nome" variante="legenda" tom="inkMuted">Piggy</Texto></View>{children}</Animated.View>;
}
const FRASES_PIGGY = ["Contando as moedinhas…", "Abrindo o cofrinho…", "Fuçando seus gastos…", "Farejando os números…", "Separando as notinhas…", "Dando uma olhada no extrato…", "Calculando aqui, um segundinho…"];
/** Em voo, antes do texto: a frase divertida troca a cada 2 s até chegar o 1º passo; depois, só o último passo. O rótulo não anuncia a frase divertida; com passo real, anuncia o passo. */
export function StatusPiggy({ passos }: { passos: string[] }) {
  const reduzir = useReduzirMovimento(); const sem = passos.length === 0;
  const [i, setI] = useState(() => Math.floor(Math.random() * FRASES_PIGGY.length));
  useEffect(() => { if (!sem) return; const id = setInterval(() => setI((n) => (n + 1) % FRASES_PIGGY.length), 2000); return () => clearInterval(id); }, [sem]);
  const texto = passos.at(-1) ?? FRASES_PIGGY[i];
  return <View accessible accessibilityLabel={sem ? "Piggy está respondendo" : `Piggy está respondendo: ${texto}`} accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
    <ActivityIndicator size="small" />
    <Animated.View key={texto} entering={reduzir ? undefined : FadeIn.duration(duracoes.feedback)}><Texto variante="legenda" tom="inkMuted">{texto}</Texto></Animated.View>
  </View>;
}
const PONTO = /^(?:- |• )(.*)$/;
/** Markdown mínimo do servidor: parágrafos (linha em branco) e itens `- `/`• `; o resto vira texto com o negrito do WhatsApp. */
export function Markdown({ texto }: { texto: string }) {
  if (!texto) return null;
  return <View style={{ gap: 8 }}>{texto.replace(/\r\n?/g, "\n").split(/\n\s*\n/).map((par, i) => {
    const blocos: { item: boolean; s: string }[] = [];
    for (const linha of par.split("\n")) {
      const m = PONTO.exec(linha), ult = blocos[blocos.length - 1];
      if (m) { if (m[1]!.trim()) blocos.push({ item: true, s: m[1]! }); } else if (ult && !ult.item) ult.s += `\n${linha}`; else blocos.push({ item: false, s: linha });
    }
    return <View key={i} style={{ gap: 4 }}>{blocos.map((b, j) => b.item
      ? <View key={j} style={{ flexDirection: "row", gap: 8 }}><Texto>•</Texto><View style={{ flex: 1 }}><ComNegrito texto={b.s} /></View></View>
      : <ComNegrito key={j} texto={b.s} />)}</View>;
  })}</View>;
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
  const emVoo = t.resposta === undefined && !t.erro && !t.interrompida, status = emVoo && !t.parcial;
  const passos = [...new Set(t.passos)].join(" · "); // os passos ficam acima do texto; enquanto o status os mostra, não duplica
  return <View onLayout={(e) => onLayout?.(e.nativeEvent.layout.y)} style={{ gap: 12 }}>
    {p.oculto ? <Texto tom="inkMuted">{OCULTA}</Texto> : <>
      <BolhaUsuario texto={t.pergunta} />
      <FalaPiggy>
        {status && <StatusPiggy passos={t.passos} />}
        {!status && !!passos && <Texto variante="legenda" tom="inkMuted">{passos}</Texto>}
        {emVoo && t.previa && t.blocos.at(-1)?.card !== t.previa && <Skeleton largura="100%" altura={120} raio={raio.bloco} />}
        {t.erro ? <Texto tom="warning">{t.erro}</Texto> : <Markdown texto={t.resposta ?? t.parcial} />}
        {t.nota && <ComNegrito texto={`Cancelei *${t.nota}* porque você saiu do chat.`} />}
        {t.interrompida && t.resposta === undefined && !t.erro && <Texto variante="legenda" tom="inkMuted">Você parou esta resposta.</Texto>}
        {card && <Bloco id={card} abrir={abrir && (() => abrir(card))}><WidgetConteudo id={card} /></Bloco>}
        {t.resposta !== undefined && <Acoes turno={t} />}
      </FalaPiggy>
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
