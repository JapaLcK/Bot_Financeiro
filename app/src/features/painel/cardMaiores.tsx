import { useState } from "react";
import { View } from "react-native";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { Icone } from "@/ui/componentes/Icone";
import { falado, VALOR_INDISPONIVEL_FALADO } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import type { RecursosBloco } from "@/api/schemas/painel";
import { TextoPainel as Texto } from "./tipografia";
import { diaMes, nomeCategoria, nomeMes } from "./catalogo";
import { Acao, Avisos, Valor, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { EntrouSaiu } from "./numerosMes";
import { LinhaCategoria } from "./gastos";
import { usePainel } from "./provider";
// Os cards que a PRÓPRIA tool do servidor emite (`get_largest_expenses` e `forecast_month_end`), só no chat: desenham SÓ o bloco.
const VISIVEIS = 5; // gastos visíveis antes do "+ N gastos"
/** "Maiores gastos · outubro de 2026" com o mês inteiro (dia 1 ao último); senão "· 10/10 a 17/10" (o ano só no dia de outro ano que o fim: "01/11/25 a 31/10"). */
export function tituloMaiores(d?: RecursosBloco["maiores_gastos"]) {
  if (!d) return "Maiores gastos";
  const mes = d.start_date.slice(0, 7), inteiro = d.start_date.endsWith("-01") && mes === d.end_date.slice(0, 7) && Number(d.end_date.slice(8)) === new Date(Date.UTC(Number(mes.slice(0, 4)), Number(mes.slice(5)), 0)).getUTCDate();
  return `Maiores gastos · ${inteiro ? nomeMes(mes) : `${diaMes(d.start_date, d.end_date)} a ${diaMes(d.end_date, d.end_date)}`}`;
}
export const tituloFimDoMes = (d?: RecursosBloco["fim_do_mes"]) => d ? `Fim do mês · ${nomeMes(d.mes)}` : "Fim do mês";
/** Os maiores lançamentos do período, um por linha (a ordem é a do servidor): título, "dd/mm · categoria · banco · parcelamento", valor e a barra relativa ao maior. */
export function CardMaiores() {
  const d = useBloco()?.recursos.maiores_gastos, p = usePainel(), { cores } = useTema(); const [todas, setTodas] = useState(false);
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const catalogo = p.dados.categorias?.fase === "pronto" ? p.dados.categorias.dado.categorias : undefined;
  const itens = d.expenses, maior = Math.max(1, ...itens.map((x) => x.valor)), resto = itens.length - VISIVEIS;
  return <View style={{ gap: 12 }}>
    {!itens.length ? <Vazio texto="Nenhum gasto neste período." /> : <View>
      {(todas ? itens : itens.slice(0, VISIVEIS)).map((x, i) => {
        const categoria = nomeCategoria(x.categoria, catalogo); // o parcelamento traz R$: some com os valores ocultos
        return <LinhaCategoria key={i} titulo={x.descricao || categoria} detalhe={[diaMes(x.data, d.end_date), categoria, x.instituicao, p.oculto ? null : x.parcelamento].filter(Boolean).join(" · ")} valor={x.valor} fracao={Math.max(0, x.valor) / maior} cor={cores.brand} />;
      })}
      {resto > 0 && <Acao rotulo={todas ? "Mostrar menos" : `+ ${resto} ${resto === 1 ? "gasto" : "gastos"}`} onPress={() => setTodas((t) => !t)} />}
    </View>}
    <Avisos motivos={d.motivos ?? []} />
  </View>;
}
/** Saldo previsto em destaque, a barra "gasto até hoje × o que falta até o fim" com o traço da receita, e Entrou/Saiu até agora. Os valores vêm do servidor; o app só calcula a parte que falta e a escala da barra. */
export function CardFimDoMes() {
  const d = useBloco()?.recursos.fim_do_mes, { oculto } = usePainel(), { cores } = useTema();
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const falta = Math.max(0, d.despesa_projetada - d.despesa), escala = Math.max(d.receita, d.despesa + falta, 1), sobra = escala - d.despesa - falta; // a escala é a maior entre a receita e o gasto previsto
  const segmentos = [{ valor: d.despesa, cor: cores.brand }, { valor: falta, cor: d.vai_fechar_negativo ? cores.alert : cores.inkFaint }, { valor: sobra, cor: "transparent" }].filter((s) => s.valor > 0);
  const rotulo = `Gasto até hoje ${falado(d.despesa) ?? VALOR_INDISPONIVEL_FALADO}, previsto até o fim do mês ${falado(d.despesa_projetada) ?? VALOR_INDISPONIVEL_FALADO}, entrou ${falado(d.receita) ?? VALOR_INDISPONIVEL_FALADO}`;
  const ponto = (cor: string, texto: string) => <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: cor }} /><Texto variante="legenda" tom="inkMuted">{texto}</Texto></View>;
  return <View style={{ gap: 12 }}>
    <View style={{ gap: 4 }}>
      <View style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between", columnGap: 12 }}>
        <Texto variante="legenda" tom="inkMuted">Saldo previsto</Texto>
        <Texto variante="legenda" tom="inkMuted">{`dia ${d.dias_corridos} de ${d.dias_no_mes}`}</Texto>
      </View>
      <Valor valor={d.saldo_projetado} destaque />
      {d.vai_fechar_negativo && !oculto && <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Icone nome="WarningCircle" tamanho={20} tom="warning" /><Texto variante="rotulo" tom="warning">Fecha no negativo</Texto></View>}
    </View>
    {!oculto && <View accessible accessibilityLabel={rotulo} style={{ gap: 8 }}>
      <View style={{ justifyContent: "center" }}>
        <BarraComposicao segmentos={segmentos} />
        {d.receita > 0 && <View testID="traco-receita" style={{ position: "absolute", left: `${(d.receita / escala) * 100}%`, marginLeft: -1, top: -3, height: 16, width: 2, borderRadius: 1, backgroundColor: cores.positive }} />}
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", columnGap: 16, rowGap: 4 }}>{ponto(cores.brand, "Até hoje")}{ponto(d.vai_fechar_negativo ? cores.alert : cores.inkFaint, "Até o fim")}{d.receita > 0 && ponto(cores.positive, "Entrou")}</View>
    </View>}
    <EntrouSaiu entrou={d.receita} saiu={d.despesa} />
    <Avisos motivos={d.motivos} />
  </View>;
}
