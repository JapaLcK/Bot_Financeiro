import { useState, type ReactNode } from "react";
import { Pressable, View } from "react-native";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { falado, VALOR_INDISPONIVEL_FALADO, VALOR_OCULTO_FALADO } from "@/ui/dinheiro";
import { selecao } from "@/ui/haptics";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { useTema } from "@/ui/tema";
import { ALFA_WASH_SERIE, raio, SERIE_META, tingir } from "@/ui/tokens";
import { nomeMes } from "./catalogo";
import { Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { avisos, DO_ALVO, fraseChegada, fraseCusto, fraseRitmo, MESES_PADRAO, type MetaV2 } from "./metasTextos";
import { usePainel } from "./provider";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import type { MetasV2 } from "@/api/schemas/painel";
const VISIVEIS = 5, POR_LINHA = 12, LADO = 20; // metas à mostra (reserva incluída); blocos por linha; largura do bloco
/** Série (0..3) da meta pelo `id`, não pela posição: mesma cor depois de reordenar. A mesma regra de `MetaPilula.tsx` da web (`data-serie`); o teste `metas_serie_paridade` compara. */
export const serieDaMeta = (id: number) => Math.abs(id) % SERIE_META.dark.length;
/** Um bloco por mês da reserva: cheio em `tom`, vazio em `border`. Quebra de linha a cada `POR_LINHA` (largura máxima da linha). */
function Blocos({ n, cheios, tom }: { n: number; cheios: number; tom: "brand" | "positive" }) {
  const { cores } = useTema();
  return <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 4, maxWidth: POR_LINHA * (LADO + 4) - 4 }}>
    {Array.from({ length: n }, (_, i) => <View key={i} testID={i < cheios ? "bloco-cheio" : "bloco-vazio"} style={{ width: LADO, height: 10, borderRadius: raio.barra, backgroundColor: i < cheios ? cores[tom] : cores.border }} />)}
  </View>;
}
/** Blocos cheios = meses cobertos: `floor((alvo-falta)*meses/alvo)` em centavos, de 0 a `meses`. Nem o `percentual` (o servidor trunca: 4/6 meses viraria 3 blocos) nem o `atual` (vem sem quantizar e o cliente arredonda: 2999,995 de 18000 encheria 1 bloco) servem de base; o `falta` já está em centavos e é a régua do "batida" (`db/metas.py`). */
const blocosCheios = (m: MetaV2, meses: number) => m.alvo === null || m.falta === null || m.alvo <= 0 ? 0 : Math.min(meses, Number(BigInt(Math.max(m.alvo - m.falta, 0)) * BigInt(meses) / BigInt(m.alvo)));
/** O que o VoiceOver lê da pílula: as frases que saíram da tela moram aqui. Com valores ocultos: sem %, sem valores, sem contagem de meses e sem a chegada (deriva do dinheiro); o prazo e os motivos (incluída a frase do ritmo que o ⚠ sinaliza) não são dinheiro e ficam. */
function rotulo(m: MetaV2, custo: MetasV2["custo_mensal"], oculto: boolean, cheios: number, meses: number): string {
  const reserva = m.tipo === "reserva", batida = m.falta === 0, dito = (c: number) => falado(c) ?? VALOR_INDISPONIVEL_FALADO;
  const mostradaRitmo = m.atual === null || (m.ritmo.valor === null && !batida), prazo = m.data_alvo ? nomeMes(m.data_alvo.slice(0, 7)) : null;
  return [
    oculto ? `${m.nome}, ${VALOR_OCULTO_FALADO}` : `${m.nome}${m.percentual === null ? "" : `, ${m.percentual}%`}`,
    reserva && [/reserva de emergência/i.test(m.nome) ? "" : "reserva de emergência", !oculto && m.atual !== null && m.alvo !== null ? `${cheios} de ${meses} meses` : ""].filter(Boolean).join(", "),
    !oculto && m.atual !== null && (m.alvo !== null ? `${dito(m.atual)} de ${dito(m.alvo)}` : `${dito(m.atual)} guardado`),
    batida && "meta batida",
    prazo && (m.prazo_passou ? `o prazo era ${prazo}` : `prazo ${prazo}`),
    !oculto && !batida && m.atual !== null && fraseChegada(m),
    mostradaRitmo && fraseRitmo(m.ritmo),
    reserva && fraseCusto(custo),
    ...avisos(m),
  ].filter(Boolean).join(", ");
}
function Pilula({ m, custo }: { m: MetaV2; custo: MetasV2["custo_mensal"] }) {
  const { cores, esquema } = useTema(); const { oculto } = usePainel(); const { ampliado } = useTipografiaPainel();
  const reserva = m.tipo === "reserva", batida = m.falta === 0, semAtual = m.atual === null, semAlvo = reserva && m.alvo === null;
  const serie = reserva ? null : SERIE_META[esquema][serieDaMeta(m.id)]!, cor = serie ?? cores.brand; // a reserva é sempre o rosa; as outras, a série
  const conferir = m.atual === 0 && m.motivos.some((c) => !DO_ALVO.includes(c));
  const meses = m.meses ?? MESES_PADRAO, cheios = oculto || semAlvo || semAtual ? 0 : blocosCheios(m, meses); // não batida (falta ≥ 1 centavo) nunca fecha todos os blocos
  const aviso = semAtual || semAlvo || conferir || avisos(m).length > 0 || (reserva && !!fraseCusto(custo)), icone: NomeIcone = reserva ? "PiggyBank" : "Target";
  return <View testID={`meta-${m.id}`} accessible accessibilityLabel={rotulo(m, custo, oculto, cheios, meses)} style={{ backgroundColor: cores.surfaceControl, borderRadius: raio.chip, minHeight: 44, paddingHorizontal: 12, paddingVertical: 8, gap: 8 }}>
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
      <View style={{ width: 32, height: 32, borderRadius: raio.pilula, backgroundColor: serie ? tingir(serie, ALFA_WASH_SERIE) : cores.brandSoft, alignItems: "center", justifyContent: "center" }}><Icone nome={icone} tamanho={20} cor={cor} /></View>
      <Texto variante="rotulo" numberOfLines={ampliado ? undefined : 1} style={{ flex: 1, minWidth: 0 }}>{m.nome}</Texto>
      {batida ? <Icone nome="CheckCircle" tamanho={20} tom="positive" /> : !oculto && !semAtual && m.percentual !== null && <Texto variante="rotulo">{m.percentual}%</Texto>}
      {aviso && <Icone nome="WarningCircle" tamanho={20} tom="inkMuted" />}
    </View>
    {reserva ? <Blocos n={meses} cheios={cheios} tom={batida ? "positive" : "brand"} />
      : !oculto && !semAtual && m.percentual !== null && <ProgressBar valor={m.percentual / 100} cor={batida ? cores.positive : cor} />}
  </View>;
}
/** Linha-botão que pede algo AO CHAT (`pedir.enviar`, como se o usuário tivesse digitado): nada navega nem abre folha. Sem `pedir` (card fora da conversa) ou com a entrada do chat desligada, fica desativada. */
function LinhaPedido({ icone, rotulo, rotuloAcessivel, pedido, children }: { icone: NomeIcone; rotulo: string; rotuloAcessivel: string; pedido: string; children?: ReactNode }) {
  const { cores } = useTema(); const pedir = useBloco()?.pedir; const off = !pedir || pedir.desativado;
  return <Pressable accessibilityRole="button" accessibilityLabel={rotuloAcessivel} accessibilityState={{ disabled: off }} disabled={off} onPress={() => { selecao(); pedir?.enviar(pedido); }} style={{ backgroundColor: cores.surfaceControl, borderRadius: raio.chip, minHeight: 44, paddingHorizontal: 12, paddingVertical: 8, gap: 8, opacity: off ? 0.5 : 1 }}>
    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><Icone nome={icone} tamanho={20} tom="brand" /><Texto variante="rotulo">{rotulo}</Texto></View>
    {children}
  </Pressable>;
}
/** Card `metas` do chat (formato v2 de `/api/v2/metas`): pílulas com ícone, % e barra (blocos na reserva), sem frases; elas só alimentam o VoiceOver. Reserva primeiro, depois as outras na ordem do servidor; mais de `VISIVEIS` linhas (a de "Montar reserva" conta): "+N" expande o resto aqui mesmo. Criar é pedir à IA no próprio chat (`LinhaPedido`); editar e apagar também são com a IA. */
export function MetasChat() {
  const d = useBloco()?.recursos.metas; const { cores } = useTema(); const [aberto, setAberto] = useState(false);
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const ordem = [...d.metas].sort((a, b) => Number(b.tipo === "reserva") - Number(a.tipo === "reserva")), semReserva = !ordem.some((m) => m.tipo === "reserva");
  const visiveis = VISIVEIS - Number(semReserva), resto = ordem.length - visiveis; // a linha "Montar reserva" ocupa a vaga da reserva
  return <View style={{ gap: 8 }}>
    {semReserva && <LinhaPedido icone="PiggyBank" rotulo="Montar reserva" rotuloAcessivel={`Montar reserva de emergência, ${MESES_PADRAO} meses, ainda não criada`} pedido="Quero montar minha reserva de emergência"><Blocos n={MESES_PADRAO} cheios={0} tom="brand" /></LinhaPedido>}
    {!ordem.length && <LinhaPedido icone="Plus" rotulo="Criar meta" rotuloAcessivel="Criar meta" pedido="Quero criar uma meta" />}
    {(aberto ? ordem : ordem.slice(0, visiveis)).map((m) => <Pilula key={m.id} m={m} custo={d.custo_mensal} />)}
    {resto > 0 && <Pressable testID="meta-mais" accessibilityRole="button" accessibilityLabel={aberto ? "Mostrar menos metas" : `+${resto}, mostrar mais ${resto} ${resto === 1 ? "meta" : "metas"}`} onPress={() => { selecao(); setAberto(!aberto); }} style={{ backgroundColor: cores.surfaceControl, borderRadius: raio.chip, minHeight: 44, alignItems: "center", justifyContent: "center", paddingHorizontal: 12 }}>
      <Texto variante="rotulo">{aberto ? "Mostrar menos" : `+${resto}`}</Texto>
    </Pressable>}
  </View>;
}
