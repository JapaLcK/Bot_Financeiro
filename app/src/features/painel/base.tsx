import { useState, type ReactNode } from "react";
import { ActivityIndicator, Animated, Modal, Platform, Pressable, ScrollView, View } from "react-native";
import Svg, { Circle, Path } from "react-native-svg";
import { usePressao, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { Card } from "@/ui/componentes/Card";
import { Money } from "@/ui/componentes/Money";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { espaco, raio, type Paleta } from "@/ui/tokens";
import { Screen } from "@/ui/componentes/Screen";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { TITULOS, type Widget } from "./catalogo";
import { usePainel, type Dados, type Recurso } from "./provider";
/**
 * Com texto, espelha os tokens do `Button secondary` (contorno `inkMuted`, texto `ink`). `texto` é o visível; `rotulo`, o do VoiceOver.
 * O contorno tem 36pt (`compacta`: 30pt, ao lado de valor), mas o alvo de toque é o `Pressable` de 44pt em volta, não `hitSlop` (o pai o recorta; ver `Chip`).
 */
export function Acao({ rotulo, texto = rotulo, onPress, icone, iconeFim, somenteIcone = false, desativado = false, compacta = false }: { rotulo: string; texto?: string; onPress: () => void; icone?: NomeIcone; iconeFim?: NomeIcone; somenteIcone?: boolean; desativado?: boolean; compacta?: boolean }) {
  const { cores } = useTema(); const pressao = usePressao();
  return <Pressable accessibilityRole="button" accessibilityLabel={rotulo} accessibilityState={{ disabled: desativado }} disabled={desativado} onPress={onPress} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} style={{ minHeight: 44, minWidth: 44, maxWidth: "100%", flexShrink: 1, alignSelf: "flex-start", justifyContent: "center" }}><Animated.View style={[{ minWidth: 44, maxWidth: "100%", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, opacity: desativado ? 0.45 : 1 }, !somenteIcone && { minHeight: compacta ? 30 : 36, borderWidth: 1, borderColor: cores.inkMuted, borderRadius: compacta ? raio.sm : raio.md, paddingHorizontal: compacta ? 10 : 14 }, pressao.estilo]}>{icone && <Icone nome={icone} tamanho={20} />}{!somenteIcone && <Texto variante="rotulo" style={[{ flexShrink: 1 }, !compacta && { fontSize: 15, lineHeight: 20 }]}>{texto}</Texto>}{iconeFim && <Icone nome={iconeFim} tamanho={20} />}</Animated.View></Pressable>;
}
export function Valor({ valor, destaque = false, pequeno = false, tipo = "saldo" }: { valor: number | null; destaque?: boolean; pequeno?: boolean; tipo?: "saldo" | "entrada" | "saida" }) {
  const { oculto } = usePainel(); const { fontScale } = useTipografiaPainel();
  return valor === null ? <Texto tom="inkMuted">Não informado</Texto> : <Money key={fontScale} centavos={valor} oculto={oculto} variante={destaque ? "display" : pequeno ? "rotulo" : "corpo"} tipo={tipo} />;
}
/** Linha de 32pt (com `children`, sem mínimo: a barra embaixo completa a altura); tocável, 44pt de alvo. */
export function Linha({ titulo, valor, tipo, detalhe, onPress, children }: { titulo: string; valor?: number | null; tipo?: "saldo" | "entrada" | "saida"; detalhe?: string; onPress?: () => void; children?: ReactNode }) {
  const { ampliado } = useTipografiaPainel(); const linhas = ampliado ? undefined : 2;
  const linha = <View style={{ flexDirection: "row", gap: 12, alignItems: "center", minHeight: children ? undefined : 32 }}><View style={{ flex: 1, minWidth: 0, gap: ampliado ? 8 : 2 }}><Texto variante="rotulo" numberOfLines={linhas}>{titulo}</Texto>{detalhe && <Texto variante="legenda" tom="inkMuted" numberOfLines={linhas}>{detalhe}</Texto>}{ampliado && valor !== undefined && <Valor valor={valor} tipo={tipo} />}</View>{!ampliado && valor !== undefined && <Valor valor={valor} tipo={tipo} />}{onPress && <Icone nome="CaretRight" tamanho={20} />}</View>;
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
/** Compromissos acompanha o horizonte da previsão (30/60/90); sem previsão pronta, o título fixo do catálogo. */
export function tituloWidget(id: Widget, dados: ReturnType<typeof usePainel>["dados"]) { const r = dados.previsao; return id === "compromissos" && r?.fase === "pronto" ? `Próximos ${r.dado.dias} dias` : TITULOS[id]; }
export function Bloco({ id, children, abrir }: { id: Widget; children: ReactNode; abrir?: () => void }) { const titulo = tituloWidget(id, usePainel().dados); return <Card><View style={{ gap: 8 }}><View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 }}><Texto variante="rotulo" tom="inkMuted" style={{ flex: 1, minWidth: 0 }}>{titulo}</Texto>{abrir && <Pressable accessibilityRole="button" accessibilityLabel={`Abrir ${titulo}`} onPress={abrir} style={{ minWidth: 44, minHeight: 44, flexShrink: 0, alignItems: "flex-end", justifyContent: "center" }}><Icone nome="ArrowSquareOut" tamanho={20} tom="inkMuted" /></Pressable>}</View>{children}</View></Card>; }
export function RecursoDados<K extends Recurso>({ nome, children }: { nome: K; children: (d: Dados[K]) => ReactNode }) {
  const p = usePainel(); const r = p.dados[nome];
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
export function Folha({ titulo, aberta, fechar, children }: { titulo: string; aberta: boolean; fechar: () => void; children: ReactNode }) {
  const { cores } = useTema(); const reduzir = useReduzirMovimento();
  return <Modal visible={aberta} animationType={reduzir ? "none" : "slide"} presentationStyle="pageSheet" allowSwipeDismissal onRequestClose={fechar}><View style={{ flex: 1, backgroundColor: cores.bg }} accessibilityViewIsModal><Screen sobCabecalho={Platform.OS === "ios"}><View style={{ gap: 20, paddingTop: espaco.lg, paddingBottom: 24 }}><View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}><Texto variante="secao" accessibilityRole="header" style={{ flex: 1 }}>{titulo}</Texto><Acao rotulo="Fechar" icone="X" somenteIcone onPress={fechar} /></View>{children}</View></Screen></View></Modal>;
}
export function Curva({ valores }: { valores: (number | null)[] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <View style={{ height: 100, justifyContent: "center" }}><Texto tom="inkMuted">Gráfico oculto</Texto></View>;
  if (!valores.length || valores.some((v) => v === null)) return <Vazio texto="Trajetória indisponível." />;
  const nums = valores as number[], min = Math.min(...nums), amplitude = Math.max(...nums) - min || 1;
  const pontos = nums.map((v, i) => `${i ? "L" : "M"}${i * 300 / Math.max(1, nums.length - 1)},${100 - (v - min) / amplitude * 85}`).join(" ");
  return <Svg width="100%" height={112} viewBox="0 0 300 112" accessible accessibilityLabel="Evolução dos valores no período"><Path d={pontos} fill="none" stroke={cores.brand} strokeWidth={3} /></Svg>;
}
/** Cor de cada categoria, pelo índice: o donut do "Para onde vai" e o que mais combinar com ele. */
export const CORES_CATEGORIA = ["brand", "positive", "warning", "inkMuted"] as const satisfies readonly (keyof Paleta)[];
export function Anel({ partes }: { partes: number[] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <Texto tom="inkMuted">Distribuição oculta</Texto>;
  const total = partes.reduce((a, b) => a + Math.max(0, b), 0); if (!total) return null;
  let acumulado = 0;
  return <Svg width={120} height={120} viewBox="0 0 120 120" accessible accessibilityLabel="Distribuição por categoria"><Circle cx={60} cy={60} r={44} stroke={cores.border} fill="none" strokeWidth={16} />{partes.map((v, i) => { const comprimento = Math.max(0, v) / total * 276.46, inicio = acumulado; acumulado += comprimento; return <Circle key={i} cx={60} cy={60} r={44} fill="none" stroke={cores[CORES_CATEGORIA[i % CORES_CATEGORIA.length]!]} strokeWidth={16} strokeDasharray={`${comprimento} ${276.46 - comprimento}`} strokeDashoffset={-inicio} rotation={-90} origin="60,60" />; })}</Svg>;
}
