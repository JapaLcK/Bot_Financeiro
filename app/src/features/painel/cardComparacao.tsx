import { View } from "react-native";
import { CurvaAcumulada } from "@/ui/componentes/CurvaAcumulada";
import { Icone } from "@/ui/componentes/Icone";
import { IconeCategoria } from "@/ui/componentes/IconeCategoria";
import { falado, MASCARA_OCULTA, partes, VALOR_INDISPONIVEL_FALADO } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import { raio, texto as escalas } from "@/ui/tokens";
import type { RecursosBloco } from "@/api/schemas/painel";
import { TextoPainel as Texto } from "./tipografia";
import { Avisos, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { curvasDaComparacao, rotulosDaComparacao } from "./comparacaoSerie";
import { usePainel } from "./provider";
import { textoPct } from "./acumuladoMes";

const CATEGORIAS = 3; // as que mais mudaram (o servidor já ordena por |delta|)
const TOM = { menos: "positive", mais: "warning", igual: "ink" } as const;
const sentido = (centavos: number) => centavos < 0 ? "menos" : centavos > 0 ? "mais" : "igual";
const semAcento = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const pctFalado = (pct: number) => `${(Math.abs(Math.round(pct * 10) / 10)).toFixed(1).replace(".", ",")} por cento`;
export const tituloComparacao = (d?: RecursosBloco["comparacao"]) => { if (!d) return "Comparação"; const r = rotulosDaComparacao(d); return `Gastos · ${r.atual} × ${r.anterior}`; };

/** Variação com sinal ("+R$ 45,00", "−R$ 800,00"): verde se gastou menos, atenção se gastou mais. Os valores ocultos viram a máscara. */
function Delta({ centavos, display = false, oculto }: { centavos: number; display?: boolean; oculto: boolean }) {
  const p = partes(Math.abs(centavos)), s = sentido(centavos);
  return <Texto variante={display ? "display" : "rotulo"} tom={oculto ? "ink" : TOM[s]} numerico style={{ fontFamily: escalas.secao.fontFamily }}>{oculto ? MASCARA_OCULTA : p ? `${s === "menos" ? "−" : s === "mais" ? "+" : ""}R$ ${p.inteiro},${p.centavos}` : "—"}</Texto>;
}

/**
 * Gasto do período contra o anterior, só no chat (`compare_periods`): o delta de DESPESA com sinal, o chip de % (cinza no mês aberto, rosa claro fechado),
 * as duas linhas acumuladas sobrepostas (`CurvaAcumulada`; sem `serie`, o card sai sem gráfico) e as 3 categorias que mais mudaram. O texto falado mora no rótulo.
 */
export function CardComparacao() {
  const d = useBloco()?.recursos.comparacao, { oculto, dados } = usePainel(), { cores } = useTema();
  if (!d?.despesa) return <Vazio texto="Dado indisponível neste card." />;
  // o bloco traz só o NOME da categoria: o ícone vem do catálogo inteiro (`/categorias`, que o provider já lê), inclusive a que zerou no período
  const catalogo = dados.categorias?.fase === "pronto" ? dados.categorias.dado.categorias : [], icone = (nome: string) => catalogo.find((c) => semAcento(c.nome) === semAcento(nome) || c.chave === semAcento(nome))?.icone ?? "tag";
  const x = d.despesa, s = sentido(x.total_delta), aberto = d.modo === "mes_aberto", r = rotulosDaComparacao(d), curvas = oculto ? null : curvasDaComparacao(d), cats = d.categorias.slice(0, CATEGORIAS);
  const valor = (c: number) => falado(Math.abs(c)) ?? VALOR_INDISPONIVEL_FALADO, frase = (c: number) => sentido(c) === "igual" ? "sem diferença" : `${valor(c)} a ${sentido(c)}`;
  const rotulo = oculto ? `Gastos de ${r.atual} contra ${r.anterior}. Valores ocultos` : `Gastos de ${r.atual} contra ${r.anterior}: ${frase(x.total_delta)}${x.pct === null ? "" : `, ${pctFalado(x.pct)}`}${aberto && d.dias_corridos ? `. Mês em andamento, dia ${d.dias_corridos} de ${d.dias_no_mes}` : ""}${cats.length ? `. Maiores mudanças: ${cats.map((c) => `${c.categoria}, ${frase(c.total_delta)}`).join("; ")}` : ""}`;
  return <View style={{ gap: 12 }}>
    <View accessible accessibilityLabel={rotulo} style={{ gap: 12 }}>
      <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <Delta centavos={x.total_delta} display oculto={oculto} />
        {!oculto && x.pct !== null && <View testID="chip-pct" style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: raio.chip, backgroundColor: aberto ? cores.surfaceControl : cores.brandSoft }}>
          {s !== "igual" && <Icone nome={s === "menos" ? "ArrowDown" : "ArrowUp"} tamanho={20} tom={aberto ? "inkMuted" : "brandInk"} />}
          <Texto variante="rotulo" tom={aberto ? "inkMuted" : "brandInk"}>{textoPct(x.pct)}</Texto>
        </View>}
      </View>
      {aberto && !!d.dias_corridos && <Texto variante="legenda" tom="inkMuted">{`dia ${d.dias_corridos} de ${d.dias_no_mes}`}</Texto>}
      {curvas && <CurvaAcumulada {...curvas} tom="brand" rotulos={r} />}
      {cats.map((c, i) => <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 32 }}>
        <IconeCategoria icone={icone(c.categoria)} />
        <Texto variante="rotulo" numberOfLines={1} style={{ flex: 1 }}>{c.categoria}</Texto>
        <Delta centavos={c.total_delta} oculto={oculto} />
      </View>)}
    </View>
    <Avisos motivos={d.motivos} />
  </View>;
}
