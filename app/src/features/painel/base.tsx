import type { ReactNode } from "react";
import { ActivityIndicator, Animated, Modal, Pressable, View } from "react-native";
import Svg, { Circle, Path } from "react-native-svg";
import { usePressao, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { Card } from "@/ui/componentes/Card";
import { Money } from "@/ui/componentes/Money";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { Screen } from "@/ui/componentes/Screen";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { TITULOS, type Widget } from "./catalogo";
import { usePainel, type Dados, type Recurso } from "./provider";
export function Acao({ rotulo, onPress, icone, somenteIcone = false, desativado = false }: { rotulo: string; onPress: () => void; icone?: NomeIcone; somenteIcone?: boolean; desativado?: boolean }) {
  const { cores } = useTema(); const pressao = usePressao();
  return <Pressable accessibilityRole="button" accessibilityLabel={rotulo} accessibilityState={{ disabled: desativado }} disabled={desativado} onPress={onPress} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} style={{ minHeight: 44, minWidth: 44, maxWidth: "100%", flexShrink: 1 }}><Animated.View style={[{ minHeight: 44, minWidth: 44, maxWidth: "100%", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, opacity: desativado ? 0.45 : 1 }, pressao.estilo]}>{icone && <Icone nome={icone} tamanho={20} />}{!somenteIcone && <Texto variante="rotulo" style={{ color: cores.inkMuted, flexShrink: 1 }}>{rotulo}</Texto>}</Animated.View></Pressable>;
}
export function Valor({ valor, destaque = false, tipo = "saldo" }: { valor: number | null; destaque?: boolean; tipo?: "saldo" | "entrada" | "saida" }) {
  const { oculto } = usePainel(); const { fontScale } = useTipografiaPainel();
  return valor === null ? <Texto tom="inkMuted">Não informado</Texto> : <Money key={fontScale} centavos={valor} oculto={oculto} variante={destaque ? "display" : "corpo"} tipo={tipo} />;
}
export function Linha({ titulo, valor, detalhe, onPress }: { titulo: string; valor?: number | null; detalhe?: string; onPress?: () => void }) {
  const { ampliado } = useTipografiaPainel();
  const corpo = <View style={{ flexDirection: "row", gap: 12, alignItems: "center", minHeight: 48 }}><View style={{ flex: 1, minWidth: 0, gap: ampliado ? 8 : 3 }}><Texto variante="rotulo">{titulo}</Texto>{detalhe && <Texto variante="legenda" tom="inkMuted">{detalhe}</Texto>}{ampliado && valor !== undefined && <Valor valor={valor} />}</View>{!ampliado && valor !== undefined && <Valor valor={valor} />}{onPress && <Icone nome="CaretRight" tamanho={20} />}</View>;
  return onPress ? <Pressable accessibilityRole="button" onPress={onPress}>{corpo}</Pressable> : corpo;
}
export function Bloco({ id, children, abrir }: { id: Widget; children: ReactNode; abrir?: () => void }) { return <Card><View style={{ gap: 16 }}><View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 }}><Texto variante="rotulo" tom="inkMuted" style={{ flex: 1, minWidth: 0 }}>{TITULOS[id]}</Texto>{abrir && <Pressable accessibilityRole="button" accessibilityLabel={`Abrir ${TITULOS[id]}`} onPress={abrir} style={{ minWidth: 44, minHeight: 44, flexShrink: 0, alignItems: "flex-end", justifyContent: "center" }}><Icone nome="ArrowSquareOut" tamanho={20} tom="inkMuted" /></Pressable>}</View>{children}</View></Card>; }
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
export function Avisos({ motivos }: { motivos: string[] }) {
 if (!motivos.length) return null;
 const mensagens = [...new Set(motivos.map((m) => MOTIVOS[m] ?? "Há informações incompletas ou a conferir neste recurso."))];
 return <Texto variante="legenda" tom="warning">{mensagens.join(" ")}</Texto>;
}
export function Vazio({ texto }: { texto: string }) { return <Texto variante="rotulo" tom="inkMuted">{texto}</Texto>; }
export function Folha({ titulo, aberta, fechar, children }: { titulo: string; aberta: boolean; fechar: () => void; children: ReactNode }) {
  const { cores } = useTema(); const reduzir = useReduzirMovimento();
  return <Modal visible={aberta} animationType={reduzir ? "none" : "slide"} presentationStyle="pageSheet" onRequestClose={fechar}><View style={{ flex: 1, backgroundColor: cores.bg }} accessibilityViewIsModal><Screen><View style={{ gap: 20, paddingBottom: 24 }}><View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}><Texto variante="secao" accessibilityRole="header" style={{ flex: 1 }}>{titulo}</Texto><Acao rotulo="Fechar" icone="X" onPress={fechar} /></View>{children}</View></Screen></View></Modal>;
}
export function Curva({ valores }: { valores: (number | null)[] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <View style={{ height: 100, justifyContent: "center" }}><Texto tom="inkMuted">Gráfico oculto</Texto></View>;
  if (!valores.length || valores.some((v) => v === null)) return <Vazio texto="Ainda não há uma trajetória completa disponível." />;
  const nums = valores as number[], min = Math.min(...nums), amplitude = Math.max(...nums) - min || 1;
  const pontos = nums.map((v, i) => `${i ? "L" : "M"}${i * 300 / Math.max(1, nums.length - 1)},${100 - (v - min) / amplitude * 85}`).join(" ");
  return <Svg width="100%" height={112} viewBox="0 0 300 112" accessible accessibilityLabel="Evolução dos valores no período"><Path d={pontos} fill="none" stroke={cores.brand} strokeWidth={3} /></Svg>;
}
export function Anel({ partes }: { partes: number[] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <Texto tom="inkMuted">Distribuição oculta</Texto>;
  const total = partes.reduce((a, b) => a + Math.max(0, b), 0); if (!total) return null;
  let acumulado = 0;
  return <Svg width={120} height={120} viewBox="0 0 120 120" accessible accessibilityLabel="Distribuição por categoria"><Circle cx={60} cy={60} r={44} stroke={cores.border} fill="none" strokeWidth={16} />{partes.map((v, i) => { const comprimento = Math.max(0, v) / total * 276.46, inicio = acumulado; acumulado += comprimento; return <Circle key={i} cx={60} cy={60} r={44} fill="none" stroke={[cores.brand, cores.positive, cores.warning, cores.inkMuted][i % 4]} strokeWidth={16} strokeDasharray={`${comprimento} ${276.46 - comprimento}`} strokeDashoffset={-inicio} rotation={-90} origin="60,60" />; })}</Svg>;
}
