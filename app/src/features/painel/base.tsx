import { useIsFocused, type Href } from "expo-router";
import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Animated, Modal, Platform, Pressable, ScrollView, View, useWindowDimensions } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Circle, Line, Path } from "react-native-svg";
import { usePressao, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { Card } from "@/ui/componentes/Card";
import { rotuloCompacto, SegurouContext } from "@/ui/componentes/CurvaAcumulada";
import { Money } from "@/ui/componentes/Money";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { CORES_CATEGORIA, espaco, raio, texto as escalas } from "@/ui/tokens";
import { Screen } from "@/ui/componentes/Screen";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import type { CardChat } from "@/api/schemas/painel";
import { TITULOS, type Widget } from "./catalogo";
import { useBloco } from "./blocoContexto";
import { useNavegacaoPainel } from "./navegacao";
import { usePrivacidade } from "./privacidade";
import { usePainel, type Dados, type Recurso } from "./provider";
/**
 * Com texto, espelha os tokens do `Button secondary` (PDF "Fantasma": fundo `surfaceControl`, sem contorno, texto `ink` SemiBold). `texto` é o visível; `rotulo`, o do VoiceOver.
 * `ativa`: filtro ligado, no par `brandSoft`/`brandInk` do `Chip` selecionado (também no botão só de ícone).
 * O botão visível tem 36pt (`compacta`: 30pt, ao lado de valor), mas o alvo de toque é o `Pressable` de 44pt em volta, não `hitSlop` (o pai o recorta; ver `Chip`).
 */
export function Acao({ rotulo, texto = rotulo, onPress, icone, iconeFim, somenteIcone = false, desativado = false, compacta = false, ativa = false }: { rotulo: string; texto?: string; onPress: () => void; icone?: NomeIcone; iconeFim?: NomeIcone; somenteIcone?: boolean; desativado?: boolean; compacta?: boolean; ativa?: boolean }) {
  const { cores } = useTema(); const pressao = usePressao();
  return <Pressable accessibilityRole="button" accessibilityLabel={rotulo} accessibilityState={{ disabled: desativado, ...(somenteIcone && { selected: ativa }) }} disabled={desativado} onPress={onPress} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} style={{ minHeight: 44, minWidth: 44, maxWidth: "100%", flexShrink: 1, alignSelf: "flex-start", justifyContent: "center" }}><Animated.View style={[{ minWidth: 44, maxWidth: "100%", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, opacity: desativado ? 0.45 : 1 }, (!somenteIcone || ativa) && { minHeight: compacta ? 30 : 36, backgroundColor: ativa ? cores.brandSoft : cores.surfaceControl, borderRadius: raio.pilula, paddingHorizontal: compacta ? 10 : 14 }, pressao.estilo]}>{icone && <Icone nome={icone} tamanho={20} tom={ativa ? "brandInk" : undefined} />}{!somenteIcone && <Texto variante="rotulo" tom={ativa ? "brandInk" : undefined} style={{ flexShrink: 1, fontFamily: escalas.secao.fontFamily }}>{texto}</Texto>}{iconeFim && <Icone nome={iconeFim} tamanho={20} tom={ativa ? "brandInk" : undefined} />}</Animated.View></Pressable>;
}
export function Valor({ valor, destaque = false, pequeno = false, tipo = "saldo", ausente }: { valor: number | null; destaque?: boolean; pequeno?: boolean; ausente?: "legenda"; tipo?: "saldo" | "entrada" | "saida" }) {
  const { oculto } = usePrivacidade(); const { fontScale } = useTipografiaPainel(); // `usePrivacidade`, não `usePainel`: a folha do simulador fica fora do `PainelProvider`
  return valor === null ? <Texto variante={ausente} tom="inkMuted">Não informado</Texto> : <Money key={fontScale} centavos={valor} oculto={oculto} variante={destaque ? "display" : pequeno ? "rotulo" : "corpo"} tipo={tipo} />;
}
/** Linha de 32pt (`inicio`: slot à esquerda, o círculo com ícone do Extrato; com `children`, sem mínimo: a barra embaixo completa a altura); tocável, 44pt de alvo. */
export function Linha({ titulo, valor, tipo, detalhe, onPress, children, inicio }: { inicio?: ReactNode; titulo: string; valor?: number | null; tipo?: "saldo" | "entrada" | "saida"; detalhe?: string; onPress?: () => void; children?: ReactNode }) {
  const { ampliado } = useTipografiaPainel(); const linhas = ampliado ? undefined : 2;
  // Linha do Extrato (`inicio`): o valor sobe para a 1ª linha, no lugar da seta, e o texto ganha a largura toda; respiro vertical para o hairline não encostar. Fonte ampliada segue a coluna de baixo.
  const linha = inicio && !ampliado
    ? <View style={{ flexDirection: "row", gap: 12, alignItems: "center", paddingVertical: 12 }}>{inicio}<View style={{ flex: 1, minWidth: 0, gap: 2 }}><View style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}><View style={{ flex: 1, minWidth: 0 }}><Texto variante="rotulo" numberOfLines={2}>{titulo}</Texto></View>{valor !== undefined && <Valor valor={valor} tipo={tipo} />}</View>{detalhe && <Texto variante="legenda" tom="inkMuted" numberOfLines={2}>{detalhe}</Texto>}</View></View>
    : <View style={{ flexDirection: "row", gap: 12, alignItems: "center", minHeight: children ? undefined : 32 }}>{inicio}<View style={{ flex: 1, minWidth: 0, gap: ampliado ? 8 : 2 }}><Texto variante="rotulo" numberOfLines={linhas}>{titulo}</Texto>{detalhe && <Texto variante="legenda" tom="inkMuted" numberOfLines={linhas}>{detalhe}</Texto>}{ampliado && valor !== undefined && <Valor valor={valor} tipo={tipo} />}</View>{!ampliado && valor !== undefined && <Valor valor={valor} tipo={tipo} />}{onPress && <Icone nome="CaretRight" tamanho={20} />}</View>;
  const corpo = children ? <View style={{ gap: 6 }}>{linha}{children}</View> : linha;
  return onPress ? <Pressable accessibilityRole="button" onPress={onPress} style={{ minHeight: 44, justifyContent: "center" }}>{corpo}</Pressable> : corpo;
}
/** Linha de filtros: rola na horizontal sem quebrar; com fonte ampliada, coluna. */
export function Filtros({ children }: { children: ReactNode }) {
  const { ampliado } = useTipografiaPainel();
  return ampliado ? <View style={{ flexDirection: "column", alignItems: "flex-start", gap: 8 }}>{children}</View> : <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>{children}</ScrollView>;
}
/** Os 7 primeiros itens e "Mostrar todos os dias (N)" / "Mostrar menos": Dia a dia e histórico do Patrimônio. */
export function SeteDias<T>({ itens, children }: { itens: T[]; children: (item: T) => ReactNode }) {
  const [todos, setTodos] = useState(false);
  return <>{(todos ? itens : itens.slice(0, 7)).map((item) => children(item))}{itens.length > 7 && <Acao rotulo={todos ? "Mostrar menos" : `Mostrar todos os dias (${itens.length})`} onPress={() => setTodos((t) => !t)} />}</>;
}
/** Compromissos acompanha o horizonte da previsão (30/60/90); sem `dias`, o título fixo do catálogo. */
export function tituloWidget(id: Widget, dias?: number) { return id === "compromissos" && dias ? `Próximos ${dias} dias` : TITULOS[id]; }
/** Horizonte da previsão do provider, se já pronta (o card do chat usa o do próprio bloco). */
export function diasDoProvider(dados: ReturnType<typeof usePainel>["dados"]) { const r = dados.previsao; return r?.fase === "pronto" ? r.dado.dias : undefined; }
const ContextoAberto = createContext<{ aberto: boolean; avisar: (pronto: boolean) => void }>({ aberto: true, avisar: () => undefined });
/**
 * O card `hero` ("Saldo previsto") do Resumo leva `detalhes`: fica compacto (`aberto` = false: gráfico, horizonte e saldo) e o card INTEIRO abre a folha com o resto ao toque. Quem está dentro lê `aberto` aqui.
 * Sem `detalhes` (o card do chat, a própria folha, os outros blocos) `aberto` é `true`: tudo inline, sem folha.
 * `avisar(true)` (o `Pronto` de `futuro.tsx`, montado só com o dado na tela) liga a seta e o toque: carregando, erro e negado não têm o que abrir. O `Pressable` fica SEMPRE montado
 * (só `disabled`): trocá-lo por uma `View` remontaria o card na troca de horizonte e perderia o foco.
 */
export const useCardAberto = () => useContext(ContextoAberto);
/**
 * `ir`: o card INTEIRO leva a `destino` (aba) ao toque. O `Pressable` de fora só captura o toque (`accessible={false}`: o VoiceOver
 * continua lendo os textos e o `Avisos` de dentro, que seria engolido por um botão único); o título é o botão que o VoiceOver anuncia.
 * Segurar no gráfico abre a bolha do dia (`CurvaAcumulada`): o gráfico não consegue tomar o toque do card (o RN só passa o responder a antepassados),
 * então avisa por `SegurouContext` e o card, ao soltar, engole esse toque em vez de navegar. Todo `onPressIn` zera o aviso: um toque novo começa limpo
 * (scrub cancelado por rolagem não solta `onPress`, mas o aviso não fica preso). Toque sem scrub, curto ou lento, navega.
 * `hero` (`detalhes`) usa o mesmo molde, mas o toque chama `detalhes` (abre a folha) em vez de navegar; botões e chips de dentro tomam o próprio toque. O card do chat não leva `ir`: nada nele abre outra tela. `titulo` substitui o do catálogo (aba com mês, card `periodo`).
 */
export function Bloco({ id, titulo: tituloDado, children, abrir, ir, acao, detalhes }: { id: CardChat; titulo?: string; children: ReactNode; abrir?: () => void; detalhes?: () => void; ir?: { destino: string; rota: Href }; acao?: ReactNode }) {
  const dias = diasDoProvider(usePainel().dados), titulo = tituloDado ?? tituloWidget(id as Widget, dias); const [pronto, avisar] = useState(false), expansivel = !!detalhes && pronto; const { navegar } = useNavegacaoPainel(); const segurou = useRef(false), zerar = () => { segurou.current = false; };
  const aoIr = () => { if (!ir) return; if (segurou.current) { segurou.current = false; return; } navegar(ir.rota); };
  const cabecalho = <View style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 8 }}><Texto variante="rotulo" tom="inkMuted" style={{ flex: 1, minWidth: 0 }}>{titulo}</Texto>{expansivel && <Icone nome="CaretRight" tamanho={20} tom="inkMuted" />}</View>;
  const card = <SegurouContext.Provider value={segurou}><ContextoAberto.Provider value={{ aberto: !detalhes, avisar }}><Card><View style={{ gap: 8 }}><View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 }}>{ir || detalhes ? <Pressable accessibilityRole="button" disabled={!ir && !expansivel} accessibilityLabel={ir ? `${titulo}, abrir ${ir.destino}` : `${titulo}, ver detalhes`} onPressIn={zerar} onPress={ir ? aoIr : detalhes} style={{ flex: 1, minHeight: 44, justifyContent: "center" }}>{cabecalho}</Pressable> : cabecalho}{acao}{abrir && <Pressable accessibilityRole="button" accessibilityLabel={`Abrir ${titulo}`} onPress={abrir} style={{ minWidth: 44, minHeight: 44, flexShrink: 0, alignItems: "flex-end", justifyContent: "center" }}><Icone nome="ArrowSquareOut" tamanho={20} tom="inkMuted" /></Pressable>}</View>{children}</View></Card></ContextoAberto.Provider></SegurouContext.Provider>;
  return ir || detalhes ? <Pressable testID={`bloco-${id}`} accessible={false} disabled={!ir && !expansivel} onPressIn={zerar} onPress={ir ? aoIr : detalhes} style={({ pressed }) => ({ opacity: pressed ? 0.7 : 1 })}>{card}</Pressable> : <View testID={`bloco-${id}`}>{card}</View>;
}
export function RecursoDados<K extends Recurso>({ nome, children }: { nome: K; children: (d: Dados[K]) => ReactNode }) {
  const p = usePainel(), bloco = useBloco(); const r = p.dados[nome];
  if (bloco) { const d = bloco.recursos[nome]; return d === undefined ? <Vazio texto="Dado indisponível neste card." /> : <>{children(d as Dados[K])}</>; } // card do chat: só o que o servidor embutiu, nunca o provider
  if (!r || r.fase === "carregando") return <ActivityIndicator accessibilityLabel="Carregando dados" />;
  if (r.fase !== "pronto") return <View style={{ gap: 8 }}><Texto variante="rotulo" tom="inkMuted">{r.mensagem}</Texto>{r.fase === "erro" && <Acao rotulo="Tentar novamente" icone="ArrowsClockwise" onPress={p.atualizar} />}</View>;
  return <>{children(r.dado)}</>;
}
const MOTIVOS: Record<string, string> = {
 arredondamento_por_grupo: "Os valores de cada grupo são arredondados. A soma exibida pode diferir em centavos do total.",
 outra_moeda: "Cobertura parcial: outras moedas não são convertidas. Categorias e dias consideram somente reais.",
 carteira_nao_confirmada: "Confira se o saldo da carteira manual está atualizado.",
 conciliacao_pendente: "Há movimentos aguardando conciliação.", movimentos_pendentes: "Há movimentos que ainda precisam ser conferidos.",
 especie_incompleta: "O dinheiro em espécie pode estar incompleto.", banco_desatualizado: "Os dados do banco precisam ser atualizados.",
 saldo_ausente: "O banco não informou um dos saldos.", moeda_presumida: "A moeda de uma conta ainda precisa ser confirmada.",
 conta_fora_do_ultimo_sync: "Uma conta não apareceu na última sincronização.", conexao_pausada: "Há uma conexão bancária pausada.",
 categorias_indisponiveis: "Não deu para ler as categorias agora.",
 inicio_do_historico: "O histórico do seu plano tem uma data de início: o que vem antes dela não entra nos números do período nem na comparação.",
};
/** Estado do banco: vai só no aviso único do topo (`TelaPainel`), nunca repetido nos cards. */
export const MOTIVOS_BANCO = new Set(["banco_desatualizado", "conexao_pausada"]);
/** Algum `motivos` (string ou `{ codigo }` da previsão), em qualquer nível, traz estado de banco? */
export function temMotivoBanco(v: unknown): boolean {
 if (Array.isArray(v)) return v.some(temMotivoBanco);
 if (!v || typeof v !== "object") return false;
 return Object.entries(v).some(([k, x]) => (k === "motivos" && Array.isArray(x) && x.some((m) => MOTIVOS_BANCO.has(typeof m === "string" ? m : m?.codigo))) || temMotivoBanco(x));
}
export function Avisos({ motivos }: { motivos: string[] }) {
 const [aberto, setAberto] = useState(false);
 const mensagens = [...new Set(motivos.filter((m) => !MOTIVOS_BANCO.has(m)).map((m) => MOTIVOS[m] ?? "Há informações incompletas ou a conferir neste recurso."))];
 if (!mensagens.length) return null;
 return <View style={{ gap: 4 }}><Pressable accessibilityRole="button" accessibilityLabel={aberto ? "Ocultar ressalvas" : "Ver ressalvas"} accessibilityState={{ expanded: aberto }} onPress={() => setAberto((a) => !a)} style={{ minWidth: 44, minHeight: 44, alignSelf: "flex-start", justifyContent: "center" }}><Icone nome="Info" tamanho={20} tom="inkMuted" /></Pressable>{aberto && <Texto variante="legenda" tom="inkMuted">{mensagens.join(" ")}</Texto>}</View>;
}
export function Vazio({ texto }: { texto: string }) { return <Texto variante="rotulo" tom="inkMuted">{texto}</Texto>; }
/**
 * `rotulo`: o que o VoiceOver lê no lugar do título (data curta → longa). `aberta && focado`: o `Modal` do RN cobre a aba de destino, então a folha some com a aba (e reaparece ao voltar: o estado segue "aberta").
 * `ancorada`: em vez do `pageSheet` (que no iOS sobe quase a tela inteira e o `Modal` não tem detents), painel colado embaixo com a ALTURA DO CONTEÚDO e teto de 80% da janela; acima disso a lista rola.
 * Fundo escurecido que fecha ao toque, grabber (arrastar para baixo fecha) e o botão Fechar. Perde o gesto nativo de arrastar o painel junto com o dedo: ele só fecha ao soltar.
 * Por que não a rota: a rota `meta` já tem a altura do conteúdo (`sheetAllowedDetents: "fitToContents"`, `app/(app)/_layout.tsx:43`) e é o precedente certo para folha de rota, com alça
 * e raio do SISTEMA. A `FolhaDia` não é rota: abre de dentro do Calendário, que está num `Modal` `pageSheet`, e a troca de dia e o `useGastosMes` são estado do provider (a rota
 * `meta` fica fora do `PainelProvider`). Aqui o `Modal` é transparente, então NÃO há alça nativa: o grabber desenhado à mão é o único (o aviso de `src/ui/componentes/Sheet.tsx:21-23`,
 * alça à mão + nativa ficam duplas, vale para quem aninhar isto numa sheet de sistema). Decisão do dono de 2026-10-18, depois do build 37.
 */
const TETO_ANCORADA = 0.8, ARRASTE_FECHA = 60;
export function Folha({ titulo, rotulo, aberta, fechar, ancorada = false, children }: { titulo: string; rotulo?: string; aberta: boolean; fechar: () => void; ancorada?: boolean; children: ReactNode }) {
  const { cores } = useTema(); const reduzir = useReduzirMovimento(); const focado = useIsFocused(); const { height } = useWindowDimensions(); const { bottom } = useSafeAreaInsets(); const inicio = useRef(0);
  const cabecalho = <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}><Texto variante="secao" accessibilityRole="header" accessibilityLabel={rotulo} style={{ flex: 1 }}>{titulo}</Texto><Acao rotulo="Fechar" icone="X" somenteIcone onPress={fechar} /></View>;
  if (ancorada) return <Modal visible={aberta && focado} transparent animationType={reduzir ? "none" : "slide"} onRequestClose={fechar}>
    <View style={{ flex: 1, justifyContent: "flex-end" }} accessibilityViewIsModal>
      <Pressable testID="folha-fundo" accessible={false} importantForAccessibility="no" onPress={fechar} style={{ position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.4)" }} />
      <View testID="folha-painel" onAccessibilityEscape={fechar} style={{ maxHeight: height * TETO_ANCORADA, backgroundColor: cores.bg, borderTopLeftRadius: raio.bloco, borderTopRightRadius: raio.bloco, paddingHorizontal: espaco.lg, paddingBottom: bottom + espaco.lg }}>
        <View testID="folha-grabber" accessible={false} importantForAccessibility="no" onTouchStart={(e) => { inicio.current = e.nativeEvent.pageY; }} onTouchEnd={(e) => { if (e.nativeEvent.pageY - inicio.current > ARRASTE_FECHA) fechar(); }} style={{ height: 24, alignItems: "center", justifyContent: "center" }}><View style={{ width: 36, height: 5, borderRadius: 3, backgroundColor: cores.border }} /></View>
        {cabecalho}
        <ScrollView style={{ flexGrow: 0 }} contentContainerStyle={{ paddingTop: 20 }} showsVerticalScrollIndicator={false}>{children}</ScrollView>
      </View>
    </View>
  </Modal>;
  return <Modal visible={aberta && focado} animationType={reduzir ? "none" : "slide"} presentationStyle="pageSheet" allowSwipeDismissal onRequestClose={fechar}><View testID="folha-sheet" style={{ flex: 1, backgroundColor: cores.bg }} accessibilityViewIsModal><Screen sobCabecalho={Platform.OS === "ios"}><View style={{ gap: 20, paddingTop: espaco.lg, paddingBottom: 24 }}>{cabecalho}{children}</View></Screen></View></Modal>;
}
/**
 * `outra`: segunda série (tracejada) na MESMA escala, p.ex. o saldo sem a compra; as duas precisam ter o mesmo tamanho e nenhum null.
 * `datas` (uma por valor): liga os eixos. Embaixo, a data do início, do meio e do fim; à esquerda, o maior, o do meio e o menor valor (`rotuloCompacto`, em centavos; um só se todos forem iguais). Os valores e as datas entram também no rótulo de acessibilidade. Sem `datas`, a curva é só o desenho.
 * `cortes`: índices de `valores` onde começa um trecho novo; a linha não liga o ponto anterior a ele (e um trecho de um ponto só vira um ponto).
 * `eixos` (com `datas`): o nome de cada eixo, em cima da coluna dos valores e embaixo das datas. Regra do dono: todo gráfico nomeia os dois eixos.
 */
const CURVA_Y0 = 15, CURVA_Y1 = 100, CURVA_ALTURA = 112, CURVA_COLUNA_Y = 52;
export function Curva({ valores, outra, datas, eixos, cortes }: { valores: (number | null)[]; outra?: (number | null)[]; datas?: string[]; cortes?: number[]; eixos?: { x: string; y: string } }) {
  const { oculto } = usePrivacidade(); const { cores } = useTema();
  if (oculto) return <View style={{ height: 100, justifyContent: "center" }}><Texto tom="inkMuted">Gráfico oculto</Texto></View>;
  if (!valores.length || valores.some((v) => v === null) || (outra && (outra.length !== valores.length || outra.some((v) => v === null)))) return <Vazio texto="Trajetória indisponível." />;
  const nums = valores as number[], todos = outra ? [...nums, ...(outra as number[])] : nums, min = Math.min(...todos), max = Math.max(...todos), amplitude = max - min || 1;
  const marcas = datas && datas.length === nums.length && nums.length > 1 ? [...new Set([0, Math.floor((nums.length - 1) / 2), nums.length - 1])] : null; // índices do início, do meio e do fim
  const eixoX = marcas?.map((i) => datas![i]!);
  const ticksY = max === min ? [max] : [max, (max + min) / 2, min]; // sem variação, um rótulo só: três iguais ficariam um por cima do outro
  const y = (v: number) => CURVA_Y1 - (v - min) / amplitude * (CURVA_Y1 - CURVA_Y0);
  const caminho = (ns: number[], quebras?: number[]) => ns.map((v, i) => `${i && !quebras?.includes(i) ? "L" : "M"}${i * 300 / Math.max(1, ns.length - 1)},${y(v)}`).join(" ");
  const tracos = <>{eixoX && ticksY.map((v, i) => <Line key={i} x1={0} x2={300} y1={y(v)} y2={y(v)} stroke={cores.border} strokeWidth={1} />)}{outra && <Path d={caminho(outra as number[])} fill="none" stroke={cores.inkMuted} strokeWidth={2} strokeDasharray="6 4" />}<Path d={caminho(nums, cortes)} fill="none" stroke={cores.brand} strokeWidth={3} />{cortes && nums.map((v, i) => (i === 0 || cortes.includes(i)) && (i === nums.length - 1 || cortes.includes(i + 1)) ? <Circle key={i} cx={i * 300 / Math.max(1, nums.length - 1)} cy={y(v)} r={3} fill={cores.brand} /> : null)}</>;
  if (!marcas || !eixoX) return <Svg width="100%" height={CURVA_ALTURA} viewBox="0 0 300 112" accessible accessibilityLabel="Evolução dos valores no período">{tracos}</Svg>;
  const rotulo = `Evolução dos valores no período: ${marcas.map((i) => `${datas![i]}, ${rotuloCompacto(nums[i]!)}`).join("; ")}`;
  const nomeEixo = { fontWeight: "600" as const };
  return <View accessible accessibilityLabel={eixos ? `${rotulo}. Eixo vertical: ${eixos.y}; eixo horizontal: ${eixos.x}` : rotulo}>
    {eixos ? <Texto testID="curva-eixo-y" variante="legenda" tom="inkMuted" style={nomeEixo}>{eixos.y}</Texto> : null}
    <View style={{ flexDirection: "row" }}>
      <View style={{ width: CURVA_COLUNA_Y, height: CURVA_ALTURA }}>
        {ticksY.map((v, i) => <Texto key={i} testID="curva-rotulo-y" variante="legenda" tom="inkMuted" numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7} style={{ position: "absolute", right: 6, top: y(v) - 8, height: 16, lineHeight: 16 }}>{rotuloCompacto(v)}</Texto>)}
      </View>
      <Svg style={{ flex: 1 }} height={CURVA_ALTURA} viewBox="0 0 300 112" preserveAspectRatio="none">{tracos}</Svg>
    </View>
    <View style={{ flexDirection: "row", justifyContent: "space-between", marginLeft: CURVA_COLUNA_Y }}>
      {eixoX.map((t, i) => <Texto key={i} testID="curva-rotulo-x" variante="legenda" tom="inkMuted">{t}</Texto>)}
    </View>
    {eixos ? <Texto testID="curva-eixo-x" variante="legenda" tom="inkMuted" style={{ ...nomeEixo, textAlign: "center", marginLeft: CURVA_COLUNA_Y }}>{eixos.x}</Texto> : null}
  </View>;
}
export function Anel({ partes }: { partes: number[] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <Texto tom="inkMuted">Distribuição oculta</Texto>;
  const total = partes.reduce((a, b) => a + Math.max(0, b), 0); if (!total) return null;
  let acumulado = 0;
  return <Svg width={120} height={120} viewBox="0 0 120 120" accessible accessibilityLabel="Distribuição por categoria"><Circle cx={60} cy={60} r={44} stroke={cores.border} fill="none" strokeWidth={16} />{partes.map((v, i) => { const comprimento = Math.max(0, v) / total * 276.46, inicio = acumulado; acumulado += comprimento; return <Circle key={i} cx={60} cy={60} r={44} fill="none" stroke={CORES_CATEGORIA[i % CORES_CATEGORIA.length]!} strokeWidth={16} strokeDasharray={`${comprimento} ${276.46 - comprimento}`} strokeDashoffset={-inicio} rotation={-90} origin="60,60" />; })}</Svg>;
}
