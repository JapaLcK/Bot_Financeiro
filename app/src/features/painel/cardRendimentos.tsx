import { useState } from "react";
import { View } from "react-native";
import type { z } from "zod";
import type { rendimentoSchema } from "@/api/schemas/painel";
import { logoDoBanco } from "@/features/openFinance/conexoes/juntar";
import { Avatar } from "@/ui/componentes/Avatar";
import { Icone } from "@/ui/componentes/Icone";
import { reais } from "@/ui/componentes/CurvaAcumulada";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { MASCARA_OCULTA } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import { ALFA_FUNDO_TOM, ALFA_WASH_SERIE, raio, SERIE_META, tingir } from "@/ui/tokens";
import { Acao, Valor, Vazio } from "./base";
import { dataCurta, diaMes } from "./catalogo";
import { LinhaExpansivel } from "./expansao";
import { FRASE_RITMO } from "./metasTextos";
import { usePainel } from "./provider";
import { TextoPainel as Texto } from "./tipografia";

type Rendimentos = NonNullable<z.output<typeof rendimentoSchema>["rendimentos"]>;
type Categoria = Rendimentos["categorias"][number];
type Periodo = "Mês" | "Total";
const ABAS = ["Mês", "Total"], ACESSIVEIS = ["Rendimento do mês", "Rendimento total desde a aplicação"];
const ATIVOS_VISIVEIS = 5;
const frase = (k: string) => FRASE_RITMO[k] as string; // as duas usadas aqui são texto fixo (não função)
const FIM = { Mês: "neste mês", Total: "desde a aplicação" } as const;

/** "+1,23%" / "−0,50%" / "0,00%" a partir do texto do servidor (só exibição); ausente ou não numérico = `null`, zero exato sem sinal (como o valor). */
const pctTexto = (pct: string | null) => { const v = Number(pct); if (!pct?.trim() || !Number.isFinite(v)) return null; return `${v === 0 ? "" : pct.startsWith("-") ? "−" : "+"}${pct.replace(/^[-+]/, "").replace(".", ",")}%`; };

/** Ganho em verde com "+", perda em `ink` com "−" (nunca vermelho, rosa ou atenção: oscilar não é erro); `null` é "Não informado", nunca zero. O desenho é do `Money`: `entrada` dá o "+" verde, `saldo` o "−" ink e o zero sem sinal. */
function Ganho({ centavos, display = false }: { centavos: number | null; display?: boolean }) {
  return <Valor valor={centavos} tipo={centavos !== null && centavos > 0 ? "entrada" : "saldo"} destaque={display} pequeno={!display} ausente="legenda" />;
}
/** "R$ 1.000,00" do saldo (texto, só exibição); `null` quando o servidor não informou. */
const saldoTexto = (c: number | null, oculto: boolean) => oculto ? MASCARA_OCULTA : c === null ? null : reais(c);

function Ativos({ ativos, periodo, oculto }: { ativos: Categoria["ativos"]; periodo: Periodo; oculto: boolean }) {
  const [todos, setTodos] = useState(false), k = periodo === "Mês" ? "mes" : "total";
  const ordenados = [...ativos].sort((a, b) => (a[k].valor === null ? 1 : 0) - (b[k].valor === null ? 1 : 0) || (b[k].valor ?? 0) - (a[k].valor ?? 0)); // nulos no fim; o resto, do que mais rendeu
  if (!ativos.length) return <Texto variante="legenda" tom="inkMuted">Não informado</Texto>;
  const visiveis = todos ? ordenados : ordenados.slice(0, ATIVOS_VISIVEIS);
  return <View style={{ gap: 4 }}>
    {visiveis.map((a, i) => <View key={i} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 }}>
      <Avatar nome={a.instituicao ?? a.nome ?? "?"} imagem={logoDoBanco({ logo_proprio: a.logo_proprio })} tamanho={32} decorativo />
      <View style={{ flex: 1, minWidth: 0 }}>
        <Texto variante="rotulo" numberOfLines={1}>{a.nome ?? "Investimento"}</Texto>
        {(a.instituicao || saldoTexto(a.saldo, oculto)) && <Texto variante="legenda" tom="inkMuted" numberOfLines={1}>{[a.instituicao, saldoTexto(a.saldo, oculto)].filter(Boolean).join(" · ")}</Texto>}
      </View>
      <View style={{ alignItems: "flex-end" }}>
        <Ganho centavos={a[k].valor} />
        {!oculto && pctTexto(a[k].pct) && <Texto variante="legenda" tom="inkMuted">{pctTexto(a[k].pct)}</Texto>}
      </View>
    </View>)}
    {ordenados.length > ATIVOS_VISIVEIS && <Acao rotulo={todos ? "Ver menos" : `Ver mais ${ordenados.length - ATIVOS_VISIVEIS}`} onPress={() => setTodos((t) => !t)} />}
  </View>;
}

/**
 * Card "Rendimentos" (`GET /rendimento` → `rendimentos`, estimado pelo saldo; a regra e os limites moram em `db/rendimentos.py`). Abre em Mês e cai para Total
 * quando o Mês não tem nenhum ativo com dado. O app só exibe: soma, base e pct vêm do servidor.
 */
export function CardRendimentos({ d: dado }: { d: z.output<typeof rendimentoSchema> }) {
  const { oculto } = usePainel(), { cores, esquema } = useTema(), [escolha, setEscolha] = useState<Periodo | null>(null), [aberta, setAberta] = useState<string | null>(null);
  const d = dado.rendimentos;
  if (!d) return <Vazio texto="Não foi possível ler seus rendimentos agora." />;
  if (d.motivos.includes("plano_sem_investimentos")) return <Vazio texto={frase("plano_sem_investimentos")} />;
  if (!d.categorias.length) return <Vazio texto={frase("nenhum_investimento")} />;
  const periodo = escolha ?? (d.mes.valor === null ? "Total" : "Mês"), k = periodo === "Mês" ? "mes" : "total", p = d[k];
  const serie = SERIE_META[esquema], total = d.categorias.reduce((s, c) => s + Math.max(c[k].valor ?? 0, 0), 0);
  const positivo = (p.valor ?? 0) > 0, saldo = saldoTexto(d.saldo, oculto);
  const quando = d.sincronizado_em ? new Date(d.sincronizado_em) : null; // data inválida: some a frase, não inventa
  const sincronizado = quando && !isNaN(quando.getTime()) ? `Open Finance sincronizado em ${dataCurta(d.sincronizado_em!)} ${quando.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}.` : null;
  const pct = pctTexto(p.pct);
  const rodape = periodo === "Mês"
    ? `Estimado pela variação do saldo desde ${diaMes(d.mes_desde, d.mes_desde)}, sem contar aportes. Ações, FIIs e fundos: só a variação de preço; dividendos não entram.`
    : "Saldo menos o valor aplicado, como o banco informa. Ações, FIIs e fundos não informam o custo e ficam sem rendimento.";
  return <View style={{ gap: 12 }}>
    <SegmentedControl opcoes={ABAS} acessiveis={ACESSIVEIS} valor={periodo} onChange={(v) => setEscolha(v as Periodo)} />
    <View style={{ gap: 4 }}>
      <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <Ganho centavos={p.valor} display />
        {!oculto && pct && <View testID="chip-pct" style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: raio.chip, backgroundColor: positivo ? tingir(cores.positive, ALFA_FUNDO_TOM[esquema]) : cores.surfaceControl }}><Texto variante="rotulo" tom={positivo ? "positive" : "ink"}>{pct}</Texto></View>}
      </View>
      <Texto variante="legenda" tom="inkMuted">{FIM[periodo]}{saldo ? ` · saldo ${saldo}` : ""}</Texto>
      {p.valor === null && periodo === "Mês" && <Texto variante="legenda" tom="inkMuted">O rendimento do mês aparece quando houver a foto do fechamento do mês anterior.</Texto>}
      {p.valor !== null && p.sem_dado > 0 && <Texto variante="legenda" tom="inkMuted">{`${p.sem_dado} de ${d.quantidade} ativos sem rendimento informado`}</Texto>}
    </View>
    {!oculto && total > 0 && <View testID="barra-categorias" accessible={false} style={{ flexDirection: "row", height: 8, borderRadius: raio.pilula, overflow: "hidden", gap: 2 }}>
      {d.categorias.map((c, i) => (c[k].valor ?? 0) > 0 && <View key={c.tipo} style={{ flex: c[k].valor!, backgroundColor: serie[i % serie.length] }} />)}
    </View>}
    <View>
      {d.categorias.map((c, i) => <LinhaExpansivel key={c.tipo} testID={`categoria-${c.tipo}`} aberta={aberta === c.tipo} alternar={() => setAberta(aberta === c.tipo ? null : c.tipo)}
        linha={<View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View testID={`tile-${c.tipo}`} style={{ width: 32, height: 32, borderRadius: raio.controle, backgroundColor: tingir(serie[i % serie.length]!, ALFA_WASH_SERIE), alignItems: "center", justifyContent: "center" }}><Icone nome="TrendUp" tamanho={20} cor={serie[i % serie.length]} /></View>
          <View style={{ flex: 1, minWidth: 0 }}><Texto variante="rotulo" numberOfLines={1}>{c.rotulo}</Texto><Texto variante="legenda" tom="inkMuted" numberOfLines={1}>{[c.ativos.length === 1 ? "1 ativo" : `${c.ativos.length} ativos`, saldoTexto(c.saldo, oculto)].filter(Boolean).join(" · ")}</Texto></View>
          <View style={{ alignItems: "flex-end" }}><Ganho centavos={c[k].valor} />{!oculto && pctTexto(c[k].pct) && <Texto variante="legenda" tom="inkMuted">{pctTexto(c[k].pct)}</Texto>}</View>
        </View>}>
        <Ativos ativos={c.ativos} periodo={periodo} oculto={oculto} />
      </LinhaExpansivel>)}
    </View>
    <Texto variante="legenda" tom="inkMuted">{[sincronizado, rodape].filter(Boolean).join(" ")}</Texto>
  </View>;
}
