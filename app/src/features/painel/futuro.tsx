import { useState } from "react";
import { View } from "react-native";
import type { z } from "zod";
import type { previsaoSchema } from "@/api/schemas/painel";
import { ChipPainel as Chip, TextoPainel as Texto } from "./tipografia";
import { dataPtBr, nomeLegivel } from "./catalogo";
import { usePainel } from "./provider";
import { useBloco } from "./blocoContexto";
import { RecursoDados, Valor, Curva, Linha, Vazio, Filtros } from "./base";
import { LinhaExpansivel } from "./expansao";
type DadosPrevisao = z.output<typeof previsaoSchema>;
type Ocorrencia = NonNullable<DadosPrevisao["compromissos"]>[number]["ocorrencias"][number];
const ddmm = (iso: string) => dataPtBr(iso).slice(0, 5);
const nome = (o: Ocorrencia, n = o.nome) => o.tipo === "fatura_cartao" ? nomeLegivel(n) : n;
const ORIGEM: Record<string, (n: number) => string> = { manual: () => "Carteira", consolidated: (n) => `Carteira + ${n} ${n === 1 ? "conta" : "contas"} do Open Finance` };
/** "Como esta previsão foi feita" (o /painel mostra num quadro à parte): fechado por padrão, porque o card do app não carrega texto explicativo (#870). Da cobertura, como o /painel, só a janela de conferência: `fontes_incompletas` são códigos sem rótulo no app. */
function ComoFoiFeita({ d }: { d: DadosPrevisao }) {
  return <View style={{ gap: 8 }}>
    <Linha titulo="Saldo de partida" valor={d.base.saldo} detalhe={ORIGEM[d.base.origem]?.(d.base.contas_bancarias) ?? "Sem saldo de partida"} />
    {d.premissas.map((t, i) => <Texto key={i} variante="legenda" tom="inkMuted">• {t}</Texto>)}
    {d.cobertura.janela_conferencia_inicio && <Texto variante="legenda" tom="inkMuted">Conferência desde {ddmm(d.cobertura.janela_conferencia_inicio)}</Texto>}
  </View>;
}
export function Previsao() {
  const p = usePainel(), bloco = useBloco(); const [aberta, setAberta] = useState<"pior" | "como" | null>(null); const alternar = (q: "pior" | "como") => setAberta((a) => a === q ? null : q);
  return <RecursoDados nome="previsao">{(d) => {
    const pior = d.pior_dia, piorLinha = pior && <Linha titulo={`Menor saldo previsto · ${dataPtBr(pior.data)}`} valor={pior.saldo} detalhe={pior.desde && pior.causas.length ? `desde ${ddmm(pior.desde)}` : undefined} />;
    return <View style={{ gap: 8 }}>
      {!bloco && <Filtros>{d.dias_permitidos.map((dias) => <Chip key={dias} rotulo={`${dias} dias`} selecionado={d.dias === dias} onPress={() => p.mudarDias(dias)} />)}</Filtros>}
      <Texto variante="legenda" tom="inkMuted">A partir de hoje, {ddmm(d.hoje)}</Texto>
      <Valor valor={d.marcos.find((m) => m.dias === d.dias)?.saldo ?? null} destaque />
      {d.estado === "indisponivel" && <Texto variante="rotulo" tom="warning">Previsão indisponível</Texto>}
      {d.base.bancos_excluidos && <Texto variante="legenda" tom="warning">Bancos conectados fora do cálculo</Texto>}
      {/* A curva parte da âncora (hoje), como a do /painel: a trajetória da API começa amanhã. */}
      {d.trajetoria ? <Curva valores={[...(d.ancora ? [d.ancora.saldo] : []), ...d.trajetoria.map((t) => t.saldo)]} /> : <Vazio texto="Trajetória indisponível no seu plano." />}
      {d.ancora && <Linha titulo="Hoje, com as premissas" valor={d.ancora.saldo} />}
      {d.marcos.map((m) => <Linha key={m.dias} titulo={`${m.dias} dias · ${dataPtBr(m.data)}`} valor={m.saldo} />)}
      {pior && (pior.causas.length ? <LinhaExpansivel testID="pior-dia" linha={piorLinha} aberta={aberta === "pior"} alternar={() => alternar("pior")}><View style={{ gap: 4 }}>{pior.causas.map((o) => <Linha key={o.chave + o.ciclo} titulo={nome(o)} valor={o.valor} tipo={o.direcao} detalhe={o.data ? ddmm(o.data) : "sem data"} />)}</View></LinhaExpansivel> : piorLinha)}
      <LinhaExpansivel testID="como-foi-feita" linha={<Texto variante="rotulo" tom="inkMuted">Como foi feita</Texto>} aberta={aberta === "como"} alternar={() => alternar("como")}><ComoFoiFeita d={d} /></LinhaExpansivel>
    </View>;
  }}</RecursoDados>;
}
/** Quando a ocorrência vence: vencida diz "venceu dd/mm", sem data diz isso. */
const quando = (o: Ocorrencia, hoje: string) => o.data === null ? "sem data" : o.data < hoje ? `venceu ${ddmm(o.data)}` : dataPtBr(o.data);
const selos = (o: Ocorrencia) => [o.qualidade_valor === "estimado" && "valor estimado", o.qualidade_data === "presumida" && "data presumida", o.realizacao === "a_conferir" ? "a conferir" : o.realizacao === "realizada" ? (o.direcao === "entrada" ? "recebido" : "pago") : null, !o.incluida_no_calculo && "fora do cálculo"];
const junta = (partes: unknown[]) => partes.filter(Boolean).join(" · ");
/**
 * Agrupados como a API manda (nunca fundidos por nome) e na ordem dela, como no /painel. Vencida ou sem data e SEM valor some: não há o que pagar
 * nem conferir nela. Dentro do grupo, por data (vencida primeiro, sem data no fim). Grupo de uma ocorrência é uma linha; de várias, abre ao toque.
 * A tela não soma dinheiro: valores iguais mostram o valor, diferentes não mostram nenhum.
 */
export function Compromissos() {
  const [aberto, setAberto] = useState<string | null>(null);
  return <RecursoDados nome="previsao">{(d) => {
    const grupos = (d.compromissos ?? []).map((g) => ({ g, oc: g.ocorrencias.filter((o) => o.valor !== null || (o.data !== null && o.data >= d.hoje)).sort((a, b) => (a.data ?? "9").localeCompare(b.data ?? "9")) })).filter((x) => x.oc.length);
    if (d.compromissos === null) return <Vazio texto="Compromissos indisponíveis no seu plano." />;
    if (!grupos.length) return <Vazio texto="Nenhum compromisso conhecido nesta janela." />;
    return <View style={{ gap: 4 }}>{grupos.map(({ g, oc }) => {
      const [o] = oc as [Ocorrencia, ...Ocorrencia[]];
      if (oc.length === 1) return <Linha key={g.chave} titulo={nome(o)} valor={o.valor} tipo={o.direcao} detalhe={junta([quando(o, d.hoje), ...selos(o)])} />;
      const iguais = oc.every((x) => x.valor === o.valor && x.direcao === o.direcao), semValor = oc.some((x) => x.valor === null), fora = oc.filter((x) => !x.incluida_no_calculo).length, ultima = oc.findLast((x) => x.data !== null)?.data; // sem data fica no fim: o intervalo vai até a última data conhecida
      const datas = o.data === null || !ultima ? "sem data" : ultima === o.data ? ddmm(o.data) : `${ddmm(o.data)} a ${ddmm(ultima)}`;
      const linha = <Linha titulo={nome(o, g.nome)} valor={semValor ? null : iguais ? o.valor : undefined} tipo={o.direcao} detalhe={junta([`${oc.length} vezes`, datas, !semValor && !iguais && "valores diferentes", oc.some((x) => x.data !== null && x.data < d.hoje) && "venceu", fora && `${fora} fora do cálculo`])} />;
      return <LinhaExpansivel key={g.chave} testID={`grupo-${g.chave}`} linha={linha} aberta={aberto === g.chave} alternar={() => setAberto(aberto === g.chave ? null : g.chave)}><View style={{ gap: 4 }}>{oc.map((x) => <Linha key={x.chave + x.ciclo} titulo={quando(x, d.hoje)} valor={x.valor} tipo={x.direcao} detalhe={junta(selos(x)) || undefined} />)}</View></LinhaExpansivel>;
    })}</View>;
  }}</RecursoDados>;
}
