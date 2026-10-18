import { useEffect, useRef } from "react";
import { AccessibilityInfo, ActivityIndicator, View } from "react-native";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { Icone } from "@/ui/componentes/Icone";
import { IconeCategoria } from "@/ui/componentes/IconeCategoria";
import { falado, faladoComSinal, VALOR_INDISPONIVEL_FALADO } from "@/ui/dinheiro";
import { CORES_CATEGORIA } from "@/ui/tokens";
import { ButtonPainel, TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { nomeMes } from "./catalogo";
import { Acao, Folha, Linha, Valor } from "./base";
import { usePainel } from "./provider";
import { useGastosMes } from "./gastosMes";
import type { Gastos } from "@/api/schemas/painel";
import type { Resultado } from "./provider";
import { contraMediaDoDia, mediaDoPeriodo, vizinho } from "./gastosDia";
import { irAoExtrato, useNavegacaoPainel } from "./navegacao";
/**
 * A folha do "Seu dia" (aba Gastos): total do dia, pílula contra a média, contador, barra de categorias com o ícone de cada fatia (na cor dela), 3 maiores (sem sinal "−", decisão do dono 2026-10-18: negativo na frente induz sensação de prejuízo) e o botão do extrato. Mínimo de texto: o dado é número e ícone.
 * Decisão SÍNCRONA de abrir (antes de qualquer leitura): o dia está no mês exibido e não antes do corte do plano. A linha tocada vem de `detalhes`, então o dia tem lançamento;
 * dia de outro mês/ano (parcela) ou antes do corte não existe em `/gastos` e vai ao extrato (nunca R$ 0,00). Não usa `saiu`, `hojeSP()` nem o `diaAbrivel`, que dependem do `/gastos`.
 */
export const abreFolha = (dia: string, mes: string, historicoDesde?: string | null) => dia.startsWith(mes) && !(historicoDesde && dia < historicoDesde);
const SEMANA = ["domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado"];
const TOM_PILULA = { gain: "positive", warn: "warning", "": "inkMuted" } as const; // tons do web (`gastosDia.ts`) → paleta do app
const semana = (dia: string) => SEMANA[new Date(`${dia}T12:00:00Z`).getUTCDay()]!;
/** "qui, 16 out" (título) e "quinta-feira, 16 de outubro" (VoiceOver). Datas civis: nunca passam pelo fuso do aparelho. */
export const dataCurta = (dia: string) => `${semana(dia).slice(0, 3)}, ${Number(dia.slice(8))} ${nomeMes(dia.slice(0, 7)).slice(0, 3)}`;
export const dataFalada = (dia: string) => `${semana(dia)}, ${Number(dia.slice(8))} de ${nomeMes(dia.slice(0, 7)).split(" de ")[0]}`;

function Corpo({ dia, g, trocar, fechar }: { dia: string; g: Resultado<Gastos> & { recarregar: () => void }; trocar: (dia: string) => void; fechar: () => void }) {
  const { navegar } = useNavegacaoPainel(); const { oculto } = usePainel(); const { ampliado } = useTipografiaPainel();
  const mes = dia.slice(0, 7), trocou = useRef(false), longa = dataFalada(dia);
  // `vizinho` precisa do `mes`, que o schema não lê. Dia antes do corte do `/gastos` é desconhecido (o `/auth/me` pode ter divergido): sem dado, nunca R$ 0,00.
  const dado = g.fase === "pronto" && g.dado.disponivel && !(g.dado.historico_desde && dia < g.dado.historico_desde) ? { ...g.dado, mes } : null;
  const entrada = dado?.dias.find((x) => x.dia === dia), total = entrada?.total.centavos ?? 0, n = entrada?.quantidade ?? 0;
  const anterior = dado && vizinho(dia, -1, dado), seguinte = dado && vizinho(dia, 1, dado);
  const agendado = !!dado && !!entrada && dia > dado.hoje;
  // Pílula: a regra do web (`GastosDiaTopo.tsx:28`): dia ANTERIOR ao `hoje` do /gastos (nunca o relógio do aparelho) e total > 0 no BRUTO; revela proporção, então some com `oculto`.
  const contra = dado && entrada && !oculto && dia < dado.hoje && Number(entrada.total.bruto) > 0 ? contraMediaDoDia(entrada, dado) : null, pct = contra?.pct ?? 0;
  const mediaFalada = contra && dado ? falado(Math.round(mediaDoPeriodo(dado).media! * 100)) ?? VALOR_INDISPONIVEL_FALADO : "";
  // Barra: a decisão "fatia > 0" e a proporção usam o texto BRUTO (`Number`), nunca os centavos (0.004 + 0.006 = 40/60); só com ≥ 2 fatias. Cor pela posição da chave no catálogo; chave fora dele, a neutra.
  const fatias = (dado && entrada && !oculto ? entrada.categorias.filter((c) => Number(c.valor.bruto) > 0) : []).map((c) => {
    const i = dado!.categorias.findIndex((x) => x.chave === c.chave), cat = dado!.categorias[i];
    return { ...c, nome: cat?.nome ?? c.chave, icone: cat?.icone ?? "tag", cor: CORES_CATEGORIA[i < 0 ? 6 : i % CORES_CATEGORIA.length]! };
  });
  // Anuncia só a troca pelas setas (a abertura já cai no título); com valores ocultos, só a data. Espera o dado do dia chegar.
  useEffect(() => {
    if (!trocou.current || g.fase !== "pronto") return;
    trocou.current = false;
    AccessibilityInfo.announceForAccessibility(oculto || !dado ? longa : `${longa}, ${faladoComSinal(total)}`);
  }, [dia, g.fase]);
  const ir = (alvo: string | null) => { if (!alvo) return; trocou.current = true; trocar(alvo); };
  const seta = (rotulo: string, icone: "CaretLeft" | "CaretRight", alvo: string | null) => <Acao rotulo={rotulo} icone={icone} somenteIcone desativado={!alvo} onPress={() => ir(alvo)} />;
  const centro = <View style={{ flex: ampliado ? undefined : 1, minWidth: 0, alignItems: "center", gap: 4 }}>
    {g.fase === "carregando" && <ActivityIndicator accessibilityLabel="Carregando dados" />}
    {(g.fase === "erro" || g.fase === "negado") && <><Texto variante="rotulo" tom="inkMuted">{g.mensagem}</Texto>{g.fase === "erro" && <Acao rotulo="Tentar novamente" icone="ArrowsClockwise" onPress={g.recarregar} />}</>}
    {dado && <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, maxWidth: "100%" }}>
      {agendado && <View accessible accessibilityLabel="Agendado"><Icone nome="CalendarBlank" /></View>}
      <View style={{ flexShrink: 1 }}><Valor valor={total} destaque /></View>
      {contra && <View testID="pilula-dia" accessible accessibilityLabel={`${pct ? `${Math.abs(pct)}% ${pct < 0 ? "abaixo da" : "acima da"}` : "na"} média do mês, ${mediaFalada} por dia`} style={{ flexDirection: "row", alignItems: "center", gap: 2, flexShrink: 0 }}>
        {pct !== 0 && <Icone nome={pct < 0 ? "ArrowDown" : "ArrowUp"} tamanho={20} tom={TOM_PILULA[contra.tom]} />}<Texto variante="rotulo" tom={TOM_PILULA[contra.tom]}>{Math.abs(pct)}%</Texto>
      </View>}
    </View>}
    {dado && n > 0 && <View accessible accessibilityLabel={`${n} ${n === 1 ? "compra" : "compras"}`} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><Icone nome="ListBullets" tamanho={20} tom="inkMuted" /><Texto variante="rotulo" tom="inkMuted">{n}</Texto></View>}
  </View>;
  const voltar = seta("Dia anterior", "CaretLeft", anterior || null), avancar = seta("Dia seguinte", "CaretRight", seguinte || null);
  return <View testID="folha-dia" style={{ gap: 16 }}>
    {ampliado ? <View style={{ gap: 8 }}>{centro}<View style={{ flexDirection: "row", justifyContent: "space-between" }}>{voltar}{avancar}</View></View>
      : <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>{voltar}{centro}{avancar}</View>}
    {fatias.length > 1 && <View accessible accessibilityLabel={fatias.map((f) => `${f.nome}, ${falado(f.valor.centavos) ?? VALOR_INDISPONIVEL_FALADO}`).join("; ")} style={{ gap: 8 }}>
      {!ampliado && <BarraComposicao segmentos={fatias.map((f) => ({ valor: Number(f.valor.bruto), cor: f.cor }))} />}
      <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 }}>{fatias.slice(0, 4).map((f, i) => <IconeCategoria key={i} icone={f.icone} cor={f.cor} />)}{fatias.length > 4 && <Texto variante="rotulo" tom="inkMuted">+{fatias.length - 4}</Texto>}</View>
    </View>}
    {dado && entrada && entrada.maiores.length > 0 && <View>{entrada.maiores.map((m, i) => <Linha key={i} titulo={oculto ? "Lançamento" : m.descricao.trim() || "Lançamento"} valor={m.valor} />)}</View>}
    <ButtonPainel rotulo="Ver extrato do dia" onPress={() => { fechar(); irAoExtrato(navegar, { dia }); }} />
  </View>;
}
/** `dia` null = fechada. A leitura de `/gastos` só começa ao abrir (`dia` não nulo) e o hook vive aqui, que o `Calendario` mantém montado enquanto `detalhes` está pronto: reabrir o mesmo mês não repete a leitura (trocar de mês ou puxar para atualizar remonta e relê; o refoco silencioso não remonta, mas a chave leva `versao` e relê). */
export function FolhaDia({ dia, mudar }: { dia: string | null; mudar: (dia: string | null) => void }) {
  const g = useGastosMes(dia && dia.slice(0, 7));
  return <Folha titulo={dia ? dataCurta(dia) : ""} rotulo={dia ? dataFalada(dia) : undefined} aberta={!!dia} ancorada fechar={() => mudar(null)}>{dia && <Corpo dia={dia} g={g} trocar={mudar} fechar={() => mudar(null)} />}</Folha>;
}
