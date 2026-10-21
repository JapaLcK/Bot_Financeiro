import { View } from "react-native";
import type { z } from "zod";
import { metasSchema } from "@/api/schemas/painel";
import { TextoPainel as Texto } from "./tipografia";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { dataPtBr, indicadorMeta } from "./catalogo";
import { usePainel } from "./provider";
import { useBloco } from "./blocoContexto";
import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";
import { MetasChat } from "./metasChat";
import { CardRendimentos } from "./cardRendimentos";
import { RecursoDados, Valor, Linha, Curva, Anel, Avisos, Vazio, SeteDias } from "./base";
export function ListaMetas({ d }: { d: z.output<typeof metasSchema> }) { const p = usePainel(); const { cores } = useTema(); return <View style={{ gap: 12 }}>{!d.goals.length && <Vazio texto="Nenhuma meta ainda." />}{d.goals.map((m) => <View key={m.id} style={{ gap: 4 }}><Linha titulo={m.name} valor={m.balance} detalhe={m.source === "open_finance" ? m.of_plan_active ? "Caixinha do banco" : "Caixinha do banco com sincronização indisponível" : "Caixinha manual"} />{m.is_goal ? <><Linha titulo="Objetivo" valor={m.target_amount} detalhe={m.target_date ? dataPtBr(m.target_date) : "Sem prazo definido"} />{!p.oculto && m.pct_complete !== null && <ProgressBar valor={m.pct_complete / 100} />}<Linha titulo="Ritmo mensal atual" valor={m.monthly_pace_current} /><Linha titulo="Ritmo mensal necessário" valor={m.monthly_pace_needed} /><View style={{ alignSelf: "flex-start", backgroundColor: cores.border, borderRadius: raio.chip, paddingHorizontal: 8, paddingVertical: 2 }}><Texto variante="legenda">{indicadorMeta(m.indicator)}</Texto></View></> : null}</View>)}</View>; }
/** Aba Metas e card `metas` do chat: o mesmo caminho (`RecursoDados` lê o provider na aba e o bloco no chat) e as mesmas pílulas. */
export function Metas() { return <RecursoDados nome="metas">{(d) => <MetasChat d={d} />}</RecursoDados>; }
/** Card `caixinhas` do chat (formato legado `{ok, goals}`, só no bloco): a mesma lista do widget de metas. */
export function Caixinhas() { const d = useBloco()?.recursos.caixinhas; return d ? <ListaMetas d={d} /> : <Vazio texto="Dado indisponível neste card." />; }
export function Patrimonio() { return <RecursoDados nome="patrimonio">{(d) => <View style={{ gap: 8 }}><Valor valor={d.total} destaque />{d.historico.length ? <><Curva valores={d.historico.map((h) => h.total)} /><Texto variante="legenda" tom="inkMuted">Histórico desde {dataPtBr(d.historico_desde ?? d.historico[0]!.dia)}</Texto><SeteDias itens={[...d.historico].sort((a, b) => b.dia.localeCompare(a.dia))}>{(h) => <Linha key={h.dia} titulo={dataPtBr(h.dia)} valor={h.total} />}</SeteDias></> : <Vazio texto="Ainda não há fotos anteriores do patrimônio." />}<Avisos motivos={[...d.motivos, ...d.historico.flatMap((h) => h.motivos)]} /></View>}</RecursoDados>; }
const LABELS = { carteira: "Carteira", bancos: "Contas bancárias", investimentos_banco: "Investimentos do banco", caixinhas: "Caixinhas", investimentos_manuais: "Investimentos manuais" };
export function Distribuicao() { return <RecursoDados nome="patrimonio">{(d) => <View style={{ gap: 8 }}><Anel partes={Object.values(d.partes)} />{(Object.keys(d.partes) as (keyof typeof LABELS)[]).map((k) => <Linha key={k} titulo={LABELS[k]} valor={d.partes[k]} />)}<Avisos motivos={d.motivos} /></View>}</RecursoDados>; }
export function Rendimento() { return <RecursoDados nome="rendimento">{(d) => <CardRendimentos d={d} />}</RecursoDados>; }
