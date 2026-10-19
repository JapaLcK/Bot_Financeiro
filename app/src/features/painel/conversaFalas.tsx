import { useEffect, useState, type ReactNode } from "react";
import { setStringAsync } from "expo-clipboard";
import { AccessibilityInfo, ActivityIndicator, Image, Platform, Pressable, View } from "react-native";
import Animated, { FadeIn } from "react-native-reanimated";
import { useIsFocused } from "expo-router";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { useToast } from "@/ui/componentes/Toast";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { Skeleton } from "@/ui/componentes/Skeleton";
import { sucesso } from "@/ui/haptics";
import { duracoes, useEntrada, useReduzirMovimento } from "@/ui/motion";
import { SIMBOLO } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import type { BlocoChat, CardChat } from "@/api/schemas/painel";
import { Bloco, tituloWidget } from "./base";
import { BlocoContexto } from "./blocoContexto";
import { mesAbrev, mesCurto, nomeMes } from "./catalogo";
import { tituloPeriodo } from "./cardPeriodo";
import { TITULO_LANCAMENTOS } from "./cardLancamentos";
import { tituloFimDoMes, tituloMaiores } from "./cardMaiores";
import { tituloComparacao } from "./cardComparacao";
import { raio, texto as escalas } from "@/ui/tokens";
import { TextoPainel as Texto } from "./tipografia";
import { usePainel } from "./provider";
import { nomeDe } from "./cabecalho";
import { cotaCheia, useConversa, type Turno } from "./conversaEstado";
import { MotivoFeedback } from "./motivoFeedback";
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
  return <Animated.View entering={entrada} testID="fala-piggy" style={{ alignSelf: "flex-start", width: "100%", gap: 6 }}>{children}</Animated.View>;
}
const FRASES_PIGGY = ["Contando as moedinhas…", "Abrindo o cofrinho…", "Fuçando seus gastos…", "Farejando os números…", "Separando as notinhas…", "Dando uma olhada no extrato…", "Calculando aqui, um segundinho…"];
/** Em voo, antes do texto: a frase divertida troca a cada 2 s até chegar o 1º passo; depois, só o último passo. O rótulo não anuncia a frase divertida; com passo real, anuncia o passo. */
export function StatusPiggy({ passos }: { passos: string[] }) {
  const reduzir = useReduzirMovimento(); const sem = passos.length === 0;
  const [i, setI] = useState(() => Math.floor(Math.random() * FRASES_PIGGY.length));
  useEffect(() => { if (!sem) return; const id = setInterval(() => setI((n) => (n + 1) % FRASES_PIGGY.length), 2000); return () => clearInterval(id); }, [sem]);
  const texto = passos.at(-1) ?? FRASES_PIGGY[i];
  const passo = passos.at(-1), focada = useIsFocused(); // iOS não tem live region: anuncia só o passo real, nunca a frase divertida, e só com a aba à vista
  useEffect(() => { if (passo && focada && Platform.OS === "ios") AccessibilityInfo.announceForAccessibility(passo); }, [passo]); // `focada` fora das deps: voltar à aba não repete o passo
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
  const nota = async (n: 1 | -1) => { if ((await c.avaliar(t, n)) === false) toast.mostrar({ mensagem: "Não conseguimos registrar sua avaliação.", tom: "erro" }); };
  return <View style={{ flexDirection: "row", marginLeft: -10 }}>
    <BotaoAcao rotulo="Copiar resposta" icone="Copy" onPress={() => void copiar()} />
    {t.messageId !== null && <><BotaoAcao rotulo="Gostei da resposta" icone="ThumbsUp" selecionado={t.feedback === 1} onPress={() => void nota(1)} /><BotaoAcao rotulo="Não gostei da resposta" icone="ThumbsDown" selecionado={t.feedback === -1} onPress={() => void nota(-1)} /></>}
  </View>;
}
/** Título do card de UMA aba: o `periodo` pelo intervalo; aba com mês ("Para onde vai · setembro de 2026"); senão o do catálogo (Compromissos com o horizonte do próprio bloco). */
export function tituloCard(card: CardChat, aba: BlocoChat["abas"][number]) {
  const periodo = aba.recursos.periodo;
  if (card === "periodo") return periodo ? tituloPeriodo(periodo.inicio, periodo.fim) : "Período";
  if (card === "caixinhas") return "Caixinhas"; // card só do chat: fora do `TITULOS`
  if (card === "maiores_gastos") return tituloMaiores(aba.recursos.maiores_gastos);
  if (card === "fim_do_mes") return tituloFimDoMes(aba.recursos.fim_do_mes);
  if (card === "comparacao") return tituloComparacao(aba.recursos.comparacao);
  if (card === "ultimos_lancamentos" || card === "sem_categoria") return TITULO_LANCAMENTOS[card];
  return aba.mes ? `${tituloWidget(card)} · ${nomeMes(aba.mes)}` : tituloWidget(card, aba.recursos.previsao?.dias);
}
/** Um card por bloco; com mais de uma aba (meses do `categorias`), abas no topo. Tudo acontece dentro do card, no chat: não há folha nem toque que abra outra tela. */
export function CardChat({ bloco }: { bloco: BlocoChat }) {
  const p = usePainel(); const c = useConversa();
  const pedir = { enviar: (texto: string) => void c.enviar(texto), desativado: p.oculto || c.enviando || c.bloqueio !== "livre" || cotaCheia(c.uso) };
  const [selecionada, setSel] = useState(0); const { abas } = bloco, sel = Math.min(selecionada, abas.length - 1), aba = abas[sel]!;
  // Rótulo único (é a identidade no `SegmentedControl`): "out" se todas são do mesmo ano, senão "out/2026".
  const mesmoAno = abas.every((a) => a.mes?.slice(0, 4) === abas[0]!.mes?.slice(0, 4)), rotulos = abas.map((a, i) => a.mes ? (mesmoAno ? mesAbrev(a.mes) : mesCurto(a.mes)) : String(i + 1));
  return <Bloco id={bloco.card} titulo={tituloCard(bloco.card, aba)}>
    {abas.length > 1 && <SegmentedControl opcoes={rotulos} acessiveis={abas.map((a, i) => a.mes ? nomeMes(a.mes) : rotulos[i]!)} valor={rotulos[sel]!} onChange={(v) => setSel(rotulos.indexOf(v))} />}
    <BlocoContexto.Provider key={sel} value={{ recursos: aba.recursos, pedir }}><WidgetConteudo id={bloco.card} /></BlocoContexto.Provider>
  </Bloco>;
}
/** Um turno: pergunta à direita, fala do Piggy à esquerda (texto parcial enquanto o stream corre) e os cards (`bloco`) que o servidor montou, antes do texto. Copiar/curtir só no `ultimo` turno. */
export function TurnoChat({ turno: t, ultimo, onLayout }: { turno: Turno; ultimo: boolean; onLayout?: (y: number) => void }) {
  const p = usePainel(); const entrada = useEntrada();
  const emVoo = t.resposta === undefined && !t.erro && !t.interrompida, status = emVoo && !t.parcial;
  const passos = [...new Set(t.passos)].join(" · "); // os passos ficam acima do texto enquanto a resposta chega; enquanto o status os mostra, não duplica; com a resposta pronta, somem
  return <View onLayout={(e) => onLayout?.(e.nativeEvent.layout.y)} style={{ gap: 12 }}>
    {p.oculto ? <Texto tom="inkMuted">{OCULTA}</Texto> : <>
      <BolhaUsuario texto={t.pergunta} />
      <FalaPiggy>
        {status && <StatusPiggy passos={t.passos} />}
        {!status && t.resposta === undefined && !!passos && <Texto variante="legenda" tom="inkMuted">{passos}</Texto>}
        {t.blocos.map((b, i) => <Animated.View key={i} entering={entrada}><CardChat bloco={b} /></Animated.View>)}
        {emVoo && t.previa && t.blocos.at(-1)?.card !== t.previa && <Skeleton largura="100%" altura={120} raio={raio.bloco} />}
        {t.erro ? <Texto tom="warning">{t.erro}</Texto> : <Markdown texto={t.resposta ?? t.parcial} />}
        {t.nota && <ComNegrito texto={`Cancelei *${t.nota}* porque você saiu do chat.`} />}
        {t.interrompida && t.resposta === undefined && !t.erro && <Texto variante="legenda" tom="inkMuted">Você parou esta resposta.</Texto>}
        {ultimo && t.resposta !== undefined && <Acoes turno={t} />}
        {ultimo && t.feedback === -1 && t.messageId !== null && <MotivoFeedback turno={t} />}
      </FalaPiggy>
    </>}
  </View>;
}
/** Chat novo: só o logo e a saudação, no centro. As sugestões moram na linha acima da caixa de entrada (conversa.tsx). */
export function EstadoVazio() {
  const p = usePainel();
  return <View style={{ flex: 1, alignItems: "center", justifyContent: "center", gap: 8 }}>
    <Image source={SIMBOLO} style={{ width: 72, height: 72 }} accessibilityIgnoresInvertColors />
    <Texto variante="titulo" accessibilityRole="header" style={{ textAlign: "center" }}>Olá, {nomeDe(p.usuario)}</Texto>
    <Texto tom="inkMuted">Como posso te ajudar?</Texto>
  </View>;
}
