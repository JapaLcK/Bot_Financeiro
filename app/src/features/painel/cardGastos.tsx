import { useState } from "react";
import { Pressable, ScrollView, View } from "react-native";
import Animated from "react-native-reanimated";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { rotuloCompacto } from "@/ui/componentes/CurvaAcumulada";
import { alturaBarra } from "@/ui/componentes/BarrasMensais";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { falado, MASCARA_OCULTA, VALOR_INDISPONIVEL_FALADO, VALOR_OCULTO_FALADO } from "@/ui/dinheiro";
import { selecao } from "@/ui/haptics";
import { useEntrada } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { CORES_CATEGORIA, raio, texto as escalas } from "@/ui/tokens";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { dataCurta, dataPtBr, nomeCategoria } from "./catalogo";
import { Avisos, Linha, Valor, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { percentualDe, somarCentavos } from "./decimal";
import { LancamentosDe, LinhaExpansivel } from "./expansao";
import { rotuloPercentual } from "./gastos";
import { usePainel } from "./provider";
// `outras` marca a coluna sintética: a chave é da categoria real, e qualquer string é nome de categoria válido.
type Item = { chave: string; nome: string; valor: number; pct: number; cor: string; outras?: true };
const VISIVEIS = 5, ALTURA = 96;
const ORIGENS = [["cartao", "CreditCard", "cartão", "ink"], ["banco", "Bank", "banco", "inkMuted"], ["carteira", "Wallet", "carteira", "inkFaint"], ["registro_antigo", "ClockCounterClockwise", "registro antigo", "border"]] as const;
const consulta = (mes: string, chave: string) => new URLSearchParams({ mes, categoria: chave, tipo: "saida", limite: "10" }).toString();
/** Uma coluna por categoria (as 4 maiores + "Outras" se passar de 5). Nenhuma abre selecionada; tocar alterna. O toque na coluna é do card. */
function Coluna({ item: c, maior, selecionada, apagada, onPress }: { item: Item; maior: number; selecionada: boolean; apagada: boolean; onPress: () => void }) {
  const { cores } = useTema(); const { oculto } = usePainel(); const { ampliado } = useTipografiaPainel();
  const pct = rotuloPercentual(c.pct, c.valor), h = oculto ? 0 : alturaBarra(c.valor, maior, ALTURA);
  return <Pressable accessibilityRole="button" accessibilityState={{ selected: selecionada }} accessibilityLabel={oculto ? `${c.nome}, ${VALOR_OCULTO_FALADO}` : `${c.nome}, ${pct}, ${falado(c.valor) ?? VALOR_INDISPONIVEL_FALADO}`} onPress={onPress}
    style={{ flex: 1, minWidth: ampliado ? 88 : 56, minHeight: 44, alignItems: "center", gap: 4, opacity: apagada ? 0.4 : 1 }}>
    {!oculto && <Texto variante="legenda" tom="inkMuted">{pct}</Texto>}
    <View style={{ width: 26, height: ALTURA, justifyContent: "flex-end", overflow: "hidden", borderRadius: raio.barra, backgroundColor: cores.surfaceControl }}>{h > 0 && <View testID="barra-coluna" style={{ height: h, borderTopLeftRadius: raio.barra, borderTopRightRadius: raio.barra, backgroundColor: c.cor }} />}</View>
    <Texto variante="legenda" numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>{oculto ? MASCARA_OCULTA : rotuloCompacto(c.valor)}</Texto>
    <Texto variante="legenda" tom={selecionada ? "ink" : "inkMuted"} numberOfLines={ampliado ? undefined : 2} style={selecionada ? { textAlign: "center", fontFamily: escalas.secao.fontFamily } : { textAlign: "center" }}>{c.nome}</Texto>
  </Pressable>;
}
/** O que "Outras" esconde: nome, % e valor de cada uma; tocar expande as transações dela (uma aberta por vez). */
function CategoriasDeOutras({ itens, mes }: { itens: Item[]; mes: string }) {
  const [aberta, setAberta] = useState<string | null>(null); const { oculto } = usePainel();
  return <View>{itens.map((c) => <LinhaExpansivel key={c.chave} linha={<Linha titulo={c.nome} detalhe={oculto ? undefined : rotuloPercentual(c.pct, c.valor)} valor={c.valor} />} aberta={aberta === c.chave} alternar={() => setAberta(aberta === c.chave ? null : c.chave)}><LancamentosDe consulta={consulta(mes, c.chave)} /></LinhaExpansivel>)}</View>;
}
/** Origem do gasto (cartão, banco, carteira, registro antigo): só ícone e %, um elemento para o VoiceOver, sem toque. */
function Origem({ origens }: { origens: Record<(typeof ORIGENS)[number][0], number> }) {
  const { cores } = useTema();
  const partes = ORIGENS.map(([k, icone, nome, tom]) => ({ icone: icone as NomeIcone, nome, tom, valor: origens[k] })).filter((o) => o.valor > 0), base = somarCentavos(partes.map((o) => o.valor));
  if (!partes.length) return null;
  const pcts = partes.map((o) => percentualDe(o.valor, base));
  return <View accessible accessibilityLabel={`Origem dos gastos: ${partes.map((o, i) => `${o.nome} ${pcts[i]}%`).join(", ")}`} style={{ gap: 8 }}>
    <BarraComposicao segmentos={partes.map((o) => ({ valor: o.valor, cor: cores[o.tom] }))} />
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 16 }}>{partes.map((o, i) => <View key={o.nome} style={{ flexDirection: "row", alignItems: "center", gap: 4 }}><Icone nome={o.icone} tamanho={20} tom="inkMuted" /><Texto variante="legenda" tom="inkMuted">{pcts[i]}%</Texto></View>)}</View>
  </View>;
}
/** Card `categorias` do chat (UMA aba por vez): total, período, colunas por categoria, transações da coluna tocada e origem. Dado = só o bloco. */
export function CardGastos() {
  const bloco = useBloco(), d = bloco?.recursos.detalhes, p = usePainel(), entrada = useEntrada();
  const [sel, setSel] = useState<number | null>(null); // índice da coluna
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
  const total = somarCentavos(d.categorias.map((c) => c.valor));
  const todas: Item[] = d.categorias.map((c, i) => ({ chave: c.categoria ?? "", nome: nomeCategoria(c.categoria, catalogo), valor: c.valor, pct: percentualDe(c.valor, total), cor: CORES_CATEGORIA[i % CORES_CATEGORIA.length]! }));
  // Até 5 categorias, todas pelo nome; com 6 ou mais, as 4 maiores e "Outras" (valor = Σ das restantes, % da soma, nunca soma de % arredondados).
  const resto = todas.length > VISIVEIS ? todas.slice(VISIVEIS - 1) : [], restoValor = somarCentavos(resto.map((c) => c.valor));
  const colunas: Item[] = resto.length ? [...todas.slice(0, VISIVEIS - 1), { chave: "", nome: "Outras", valor: restoValor, pct: percentualDe(restoValor, total), cor: CORES_CATEGORIA[6], outras: true }] : todas;
  const escolhida = sel === null ? undefined : colunas[sel], maior = Math.max(...colunas.map((c) => c.valor)), intervalo = `${dataPtBr(`${d.mes}-01`).slice(0, 5)}–${dataPtBr(d.ate).slice(0, 5)}`;
  // A sincronização mais antiga entre as contas CONECTADAS (a pausada ou removida vem com `conexao_pausada`) que têm data; sem nenhuma, sem ícone.
  const sinc = bloco?.recursos.contas?.contas.filter((c) => !c.motivos.includes("conexao_pausada")).map((c) => c.sincronizado_em).filter((s): s is string => !!s && !Number.isNaN(Date.parse(s))).sort((a, b) => Date.parse(a) - Date.parse(b))[0];
  return <View style={{ gap: 12 }}>
    {colunas.length > 0 && <Valor valor={total} destaque />}
    <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 16, alignItems: "center" }}>
      <View accessible accessibilityLabel={`Período: ${intervalo.replace("–", " a ")}`} style={{ flexDirection: "row", alignItems: "center", gap: 4, minHeight: 44 }}><Icone nome="CalendarBlank" tamanho={20} tom="inkMuted" /><Texto variante="legenda" tom="inkMuted">{intervalo}</Texto></View>
      {sinc && <View accessible accessibilityLabel={`Sincronizado em ${dataCurta(sinc)}`} style={{ flexDirection: "row", alignItems: "center", gap: 4, minHeight: 44 }}><Icone nome="ArrowsClockwise" tamanho={20} tom="inkMuted" /><Texto variante="legenda" tom="inkMuted">{dataCurta(sinc)}</Texto></View>}
    </View>
    <Avisos motivos={d.motivos} />
    {!colunas.length ? <Vazio texto="Nenhum gasto em reais registrado neste período." /> : <>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ minWidth: "100%" }}>
        <View style={{ flexDirection: "row", flexGrow: 1, gap: 4 }}>{colunas.map((c, i) => <Coluna key={i} item={c} maior={maior} selecionada={sel === i} apagada={sel !== null && sel !== i} onPress={() => { selecao(); setSel(sel === i ? null : i); }} />)}</View>
      </ScrollView>
      {escolhida && <Animated.View key={sel} testID="gastos-transacoes" entering={entrada}>{escolhida.outras ? <CategoriasDeOutras itens={resto} mes={d.mes} /> : <LancamentosDe consulta={consulta(d.mes, escolhida.chave)} />}</Animated.View>}
      {d.origens && !p.oculto && <Origem origens={d.origens} />}
    </>}
  </View>;
}
