import { irAoExtrato, useNavegacaoPainel } from "./navegacao";
import { useEffect, useState } from "react";
import { Pressable, View } from "react-native";
import Slider from "@react-native-community/slider";
import { useTema } from "@/ui/tema";
import { Icone } from "@/ui/componentes/Icone";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { diaMes, nomeCategoria } from "./catalogo";
import { usePainel } from "./provider";
import { RecursoDados, Anel, Linha, Avisos, Acao, Vazio, Valor, SeteDias } from "./base";
import { useBloco } from "./blocoContexto";
import { LancamentosDe, LinhaExpansivel } from "./expansao";
import { FolhaDia, abreFolha } from "./folhaDia";
import { CORES_CATEGORIA, raio } from "@/ui/tokens";
import { somarCentavos, percentualEmCentavos } from "./decimal";
// Barra da cor da fatia no donut, logo abaixo do nome (linha + barra = 44pt). Decorativa: o valor já está na linha. `fracao` é 0–1.
export function LinhaCategoria({ titulo, detalhe, valor, fracao, cor, onPress }: { titulo: string; detalhe?: string; valor: number; fracao: number; cor: string; onPress?: () => void }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  return <Linha titulo={titulo} detalhe={detalhe} valor={valor} onPress={onPress}>{!oculto && <View accessibilityElementsHidden importantForAccessibility="no" style={{ height: 6, borderRadius: raio.pilula, backgroundColor: cores.border }}><View style={{ height: 6, borderRadius: raio.pilula, width: `${fracao * 100}%`, backgroundColor: cor }} /></View>}</Linha>;
}
/** "30%"; "<1%" quando o inteiro arredonda a 0 mas há valor. */
export const rotuloPercentual = (pct: number, valor: number) => `${pct === 0 && valor > 0 ? "<1" : pct}%`;
// Só o painel: o card do chat desenha `CardGastos`.
export function Categorias() { const { navegar } = useNavegacaoPainel(); const p = usePainel(); const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined; return <RecursoDados nome="detalhes">{(d) => { const total = somarCentavos(d.categorias.map((c) => c.valor)); return <View style={{ gap: 8 }}>{!d.categorias.length ? <Vazio texto="Nenhum gasto em reais registrado neste período." /> : <><View style={{ flexDirection: "row", gap: 20, alignItems: "center" }}><Anel partes={d.categorias.map((c) => c.valor)} /><View style={{ flex: 1, gap: 4 }}><Texto variante="legenda" tom="inkMuted">Gastos em reais</Texto><Valor valor={total} /></View></View><View>{d.categorias.map((c, i) => <View key={c.categoria ?? i}><LinhaCategoria titulo={nomeCategoria(c.categoria, catalogo)} valor={c.valor} fracao={Math.max(0, c.valor) / Math.max(1, total)} cor={CORES_CATEGORIA[i % CORES_CATEGORIA.length]!} onPress={() => irAoExtrato(navegar, { categoria: c.categoria ?? "" })} /></View>)}</View></>}<Avisos motivos={d.motivos} /></View>; }}</RecursoDados>; }
// Uma linha por dia, mais recente primeiro. Sem `accessibilityLabel` no botão: o VoiceOver lê data e valores dos filhos.
// As barras são `View` decorativa (não `ProgressBar`, que seria anunciada duas vezes por dia) e somem com valores ocultos.
export function Calendario() {
 const { navegar } = useNavegacaoPainel(); const { ampliado } = useTipografiaPainel(); const p = usePainel(); const { cores } = useTema(); const bloco = useBloco(); const [aberto, setAberto] = useState<string | null>(null), [diaFolha, setDiaFolha] = useState<string | null>(null);
 useEffect(() => setDiaFolha(null), [p.mes]); // a folha é do mês exibido
 return <RecursoDados nome="detalhes">{(d) => { const dias = [...d.dias].sort((a, b) => b.dia.localeCompare(a.dia)), maximo = Math.max(1, ...dias.map((x) => Math.max(x.entrou, x.saiu))), barras = !ampliado && !p.oculto;
  // A compra parcelada conserva o dia original (db/mes_detalhes.py): dia de outro ano leva o ano, senão dois "03/10" se confundem.
  const outroAno = (dia: string) => dia.slice(0, 4) !== d.mes.slice(0, 4), largura = dias.some((x) => outroAno(x.dia)) ? 68 : 44;
  const barra = (valor: number, cor: string) => <View style={{ height: 6, borderRadius: raio.pilula, backgroundColor: cor, width: `${Math.max(0, valor) / maximo * 100}%` }} />;
  return <View style={{ gap: 8 }}>{dias.length ? <View style={{ gap: 4 }}><SeteDias itens={dias}>{(dia) => { const conteudo = <View style={{ flex: 1, minWidth: 0, flexDirection: ampliado ? "column" : "row", alignItems: ampliado ? "flex-start" : "center", gap: ampliado ? 2 : 12 }}><Texto variante="rotulo" style={ampliado ? undefined : { width: largura }}>{diaMes(dia.dia, d.mes)}</Texto>{barras && <View accessibilityElementsHidden importantForAccessibility="no" style={{ flex: 1, gap: 4 }}>{barra(dia.entrou, cores.positive)}{barra(dia.saiu, cores.alert)}</View>}<View style={{ alignItems: ampliado ? "flex-start" : "flex-end", flex: barras || ampliado ? undefined : 1 }}>{dia.entrou !== 0 && <Valor valor={dia.entrou} tipo="entrada" pequeno />}{dia.saiu !== 0 && <Valor valor={dia.saiu} tipo="saida" pequeno />}{!dia.entrou && !dia.saiu && <Valor valor={0} pequeno />}</View></View>;
   // No chat o dia expande os lançamentos dele (`?dia=` do endpoint, dentro do mês do bloco: a parcela de outro ano também casa), 10 por vez.
   return bloco ? <LinhaExpansivel key={dia.dia} testID={`calendario-dia-${dia.dia}`} linha={conteudo} aberta={aberto === dia.dia} alternar={() => setAberto(aberto === dia.dia ? null : dia.dia)}><LancamentosDe consulta={new URLSearchParams({ mes: d.mes, dia: dia.dia, limite: "10" }).toString()} /></LinhaExpansivel>
    : <Pressable key={dia.dia} testID={`calendario-dia-${dia.dia}`} accessibilityRole="button" onPress={() => abreFolha(dia.dia, d.mes, p.usuario.history_earliest_date) ? setDiaFolha(dia.dia) : irAoExtrato(navegar, { dia: dia.dia })} style={{ minHeight: 44, flexDirection: "row", alignItems: "center", gap: 12 }}>{conteudo}<Icone nome="CaretRight" tamanho={20} /></Pressable>; }}</SeteDias></View> : <Vazio texto="Ainda não há movimentos neste período." />}<Avisos motivos={d.motivos} />{!bloco && <FolhaDia dia={diaFolha} mudar={setDiaFolha} />}</View>; }}</RecursoDados>;
}
// Slider nativo (UISlider): ajustável no VoiceOver sem código. O valor chega como `number` qualquer e
// `percentualEmCentavos` lança com percentual não inteiro, daí o arredondamento ao passo antes do estado.
export function Simulador() {
 const { ampliado } = useTipografiaPainel(); const { cores } = useTema();
 const p = usePainel(); const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined; const [cortes, setCortes] = useState<Record<string, number>>({});
 return <RecursoDados nome="detalhes">{(d) => { const economias = d.categorias.map((c) => percentualEmCentavos(c.valor, cortes[c.categoria ?? ""] ?? 0)); return <View style={{ gap: 8 }}>{d.categorias.length ? d.categorias.map((c, i) => { const chave = c.categoria ?? "", corte = cortes[chave] ?? 0, nome = nomeCategoria(c.categoria, catalogo); return <View key={chave}><View style={{ flexDirection: "row", flexWrap: ampliado ? "wrap" : "nowrap", justifyContent: "space-between", alignItems: "center", columnGap: 8 }}><Texto variante="rotulo" numberOfLines={ampliado ? undefined : 1} style={{ flexShrink: 1 }}>{nome}</Texto><View style={{ flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 0 }}><Texto variante="rotulo" tom="inkMuted">{corte}%</Texto><Texto variante="rotulo" tom="inkMuted">·</Texto><Valor valor={economias[i]!} pequeno /></View></View><Slider accessibilityLabel={`Corte em ${nome}`} style={{ height: 40 }} minimumValue={0} maximumValue={100} step={10} value={corte} minimumTrackTintColor={CORES_CATEGORIA[i % CORES_CATEGORIA.length]!} maximumTrackTintColor={cores.surfaceRaised} onValueChange={(v) => setCortes((s) => ({ ...s, [chave]: Math.round(v / 10) * 10 }))} /></View>; }) : <Vazio texto="Sem gastos neste mês." />}<Texto variante="legenda" tom="inkMuted">Economia potencial</Texto><Valor valor={somarCentavos(economias)} destaque /><Acao rotulo="Zerar simulação" onPress={() => setCortes({})} /><Avisos motivos={d.motivos} /></View>; }}</RecursoDados>;
}
