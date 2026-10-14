import { StyleSheet, View } from "react-native";
import { Icone } from "@/ui/componentes/Icone";
import { falado, MASCARA_OCULTA, partes, VALOR_OCULTO_FALADO } from "@/ui/dinheiro";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";
import { nomeMes } from "./catalogo";
import { Valor, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { avisos, DO_ALVO, fraseChegada, fraseCusto, fraseRitmo, MESES_PADRAO, type MetaV2 } from "./metasTextos";
import { usePainel } from "./provider";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import type { MetasV2 } from "@/api/schemas/painel";
/** Frase com um valor no meio ("Falta R$ X", "R$ X/mês até junho de 2027"): quebra de linha livre, sem caber em uma coluna só. `mes`: o "/mês" é lido "por mês". */
function ComValor({ antes, valor, tipo, mes, depois }: { antes?: string; valor: number; tipo?: "saldo" | "entrada"; mes?: boolean; depois?: string }) {
  return <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "baseline", columnGap: 4 }}>
    {!!antes && <Texto variante="rotulo">{antes}</Texto>}<Valor valor={valor} tipo={tipo} pequeno />
    {mes && <Texto variante="rotulo" accessibilityLabel="por mês">/mês</Texto>}{!!depois && <Texto variante="rotulo">{depois}</Texto>}
  </View>;
}
function Selo({ texto }: { texto: string }) {
  const { cores } = useTema();
  return <View style={{ alignSelf: "flex-start", backgroundColor: cores.border, borderRadius: raio.chip, paddingHorizontal: 8, paddingVertical: 2 }}><Texto variante="legenda">{texto}</Texto></View>;
}
const Nota = ({ texto, rotulo }: { texto: string; rotulo?: string }) => <Texto variante="legenda" tom="inkMuted" accessibilityLabel={rotulo}>{texto}</Texto>;
const dias = (n: number | null) => n === null ? "medição parcial" : `medido em ${n} ${n === 1 ? "dia" : "dias"}`;
function Precisa({ m }: { m: MetaV2 }) {
  if (!m.data_alvo) return <Nota texto="Sem prazo. Defina uma data no painel e eu digo quanto guardar por mês." />;
  const prazo = nomeMes(m.data_alvo.slice(0, 7));
  if (m.prazo_passou) return <Texto variante="rotulo">O prazo era {prazo}</Texto>;
  return m.por_mes === null ? <Texto variante="rotulo">Prazo: {prazo}</Texto> : <ComValor valor={m.por_mes} mes depois={`até ${prazo}`} />;
}
function Ritmo({ m }: { m: MetaV2 }) {
  const v = m.ritmo.valor;
  if (m.atual === null) return <Texto variante="rotulo">—</Texto>;
  if (v === null) return <Nota texto={fraseRitmo(m.ritmo)} />;
  return <View style={{ gap: 4 }}><ComValor valor={v} tipo={v > 0 ? "entrada" : "saldo"} mes /><Nota texto="inclui rendimento e variação" />{m.ritmo.parcial && <Selo texto={dias(m.ritmo.dias_de_historico)} />}</View>;
}
function Meta({ m, custo }: { m: MetaV2; custo: MetasV2["custo_mensal"] }) {
  const { ampliado } = useTipografiaPainel(); const { oculto } = usePainel();
  const reserva = m.tipo === "reserva", batida = m.falta === 0, semAtual = m.atual === null;
  const conferir = m.atual === 0 && m.motivos.some((c) => !DO_ALVO.includes(c));
  const aviso = reserva && m.alvo !== null ? fraseCusto(custo) : null; // com valor, o motivo vira "o valor pode estar velho"
  const p = custo.valor ? partes(custo.valor) : null; // mesma máscara e mesmo rótulo de valor oculto do `Money`
  const linha = (c: string) => { // `c`: o custo como aparece (visível) ou como é lido (falado)
    const conta = reserva && m.alvo !== null && p ? ` (${m.meses} × ${c})` : "";
    return reserva ? `Reserva de emergência · ${m.meses} ${m.meses === 1 ? "mês" : "meses"} das suas contas fixas${conta}${m.fonte.rotulo ? ` · ${m.fonte.rotulo}` : ""}` : m.fonte.rotulo;
  };
  const sub = linha(oculto ? MASCARA_OCULTA : `R$ ${p?.inteiro},${p?.centavos}`), falada = linha(oculto ? VALOR_OCULTO_FALADO : falado(custo.valor ?? 0) ?? "");
  return <View testID={`meta-${m.id}`} style={{ gap: 6 }}>
    <Texto variante="rotulo" accessibilityRole="header" numberOfLines={ampliado ? undefined : 2}>{m.nome}</Texto>
    {!!sub && <Nota texto={sub} rotulo={falada ?? undefined} />}
    {!!aviso && <Nota texto={aviso} />}
    <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "baseline", columnGap: 4, rowGap: 4 }}>
      {semAtual ? <Texto variante="rotulo">—</Texto> : <Valor valor={m.atual} pequeno />}
      {m.alvo !== null ? <><Texto variante="rotulo">de</Texto><Valor valor={m.alvo} pequeno /></> : !semAtual && <Texto variante="rotulo">guardado</Texto>}
      {conferir && <Selo texto="a conferir" />}
    </View>
    {semAtual && <Nota texto={fraseRitmo(m.ritmo)} />}
    {!oculto && m.percentual !== null && <ProgressBar valor={m.percentual / 100} tom={batida ? "positive" : "brand"} />}
    {batida ? <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Icone nome="CheckCircle" tamanho={20} tom="positive" /><Texto variante="rotulo" tom="positive">Meta batida</Texto></View>
      : m.falta !== null && <ComValor antes="Falta" valor={m.falta} />}
    {reserva && m.alvo === null && !!fraseCusto(custo) && <Nota texto={fraseCusto(custo)!} />}
    {!batida && <View style={{ flexDirection: ampliado ? "column" : "row", gap: ampliado ? 8 : 16 }}>
      <View style={{ flex: 1, gap: 4 }}><Nota texto="Precisa por mês" /><Precisa m={m} /></View>
      <View style={{ flex: 1, gap: 4 }}><Nota texto="Seu ritmo" /><Ritmo m={m} /></View>
    </View>}
    {!batida && !semAtual && !!fraseChegada(m) && <Nota texto={fraseChegada(m)!} />}
    {avisos(m).map((a) => <Nota key={a} texto={a} />)}
  </View>;
}
/**
 * Card `metas` do chat (formato v2 de `/api/v2/metas`), só leitura: desenha o que o servidor mandou, sem somar nem derivar dinheiro. Reserva primeiro,
 * depois as outras na ordem do servidor. Criar e editar é no painel (web): nenhum botão aqui, o toque é do `Bloco` (abre a Folha com o mesmo bloco).
 */
export function MetasChat() {
  const d = useBloco()?.recursos.metas; const { cores } = useTema();
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const ordem = [...d.metas].sort((a, b) => Number(b.tipo === "reserva") - Number(a.tipo === "reserva")), custo = d.custo_mensal, aviso = fraseCusto(custo);
  return <View style={{ gap: 12 }}>
    {!ordem.some((m) => m.tipo === "reserva") && <View style={{ gap: 4 }}>
      <Texto variante="rotulo">Reserva de emergência: {MESES_PADRAO} meses das suas contas fixas. Monte no painel.</Texto>
      {custo.valor !== null && <ComValor valor={custo.valor} mes />}{!!aviso && <Nota texto={aviso} />}
    </View>}
    {!ordem.length && <Vazio texto="Você ainda não tem metas. Crie a primeira no painel: pigbankai.com/painel." />}
    {ordem.map((m, i) => <View key={m.id}>{i > 0 && <View style={{ height: StyleSheet.hairlineWidth, backgroundColor: cores.border, marginBottom: 12 }} />}<Meta m={m} custo={custo} /></View>)}
  </View>;
}
