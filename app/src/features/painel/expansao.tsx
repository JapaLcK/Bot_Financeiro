import { useEffect, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import Animated from "react-native-reanimated";
import { Icone } from "@/ui/componentes/Icone";
import { useEntrada } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { TextoPainel as Texto } from "./tipografia";
import { Acao, Vazio } from "./base";
import { LinhaLancamento, useLancamentos } from "./extrato";
import { usePainel } from "./provider";
/**
 * Linha de card do chat que, ao toque, abre os detalhes DENTRO do card (o chat nunca navega nem responde só texto). O botão é a `linha` toda (+ seta);
 * o conteúdo abre logo abaixo. `CaretUp` não está no subset de ícones: a mesma seta gira 180°. Quem usa guarda UMA linha aberta por card.
 */
export function LinhaExpansivel({ linha, aberta, alternar, children, testID }: { linha: ReactNode; aberta: boolean; alternar: () => void; children: ReactNode; testID?: string }) {
  const entrada = useEntrada(); const { cores } = useTema();
  return <View>
    <Pressable testID={testID} accessibilityRole="button" accessibilityState={{ expanded: aberta }} onPress={alternar} style={{ minHeight: 44, flexDirection: "row", alignItems: "center", gap: 12 }}>
      <View style={{ flex: 1, minWidth: 0 }}>{linha}</View>
      <View style={{ transform: [{ rotate: aberta ? "180deg" : "0deg" }] }}><Icone nome="CaretDown" tamanho={20} /></View>
    </Pressable>
    {aberta && <Animated.View testID="linha-expandida" entering={entrada} style={{ marginLeft: 4, paddingLeft: 12, paddingBottom: 8, borderLeftWidth: 2, borderLeftColor: cores.border }}>{children}</Animated.View>}
  </View>;
}
const TETO_PAGINAS = 20; // do filtro por dia: a 1ª página + 20 = 21 requisições, até 2100 lançamentos do mês
/**
 * Lançamentos da `consulta` (paginados, só leitura), uma linha aberta por vez. Monta ao expandir e desmonta ao recolher: o cleanup do `useLancamentos` aborta a busca.
 * `filtroDia`: o endpoint não filtra por dia (ordem `dia desc`), então pagina o mês e fica com `data === dia`, parando quando o último item já é anterior ao dia.
 */
export function LancamentosDe({ consulta, filtroDia }: { consulta: string; filtroDia?: string }) {
  const p = usePainel(); const l = useLancamentos(consulta); const [aberto, setAberto] = useState<string | null>(null);
  const [parou, setParou] = useState(false); const ultimo = useRef<string | null>(null); const paginas = useRef(0);
  // ponytail: pior caso (dia no começo do mês) percorre o mês inteiro, 100 por página; a saída é um filtro `dia` no endpoint. Teto de TETO_PAGINAS e parada se o servidor repetir o cursor (senão, laço sem fim)
  useEffect(() => { ultimo.current = null; paginas.current = 0; setParou(false); }, [p.usuario.user_id, p.versao, consulta]); // a lista reinicia nestas chaves (useLancamentos): cursor, contagem e parada recomeçam com ela
  const seguir = !!filtroDia && !!l.proximo && !l.erro && !parou && (l.itens.at(-1)?.data ?? "") >= filtroDia;
  useEffect(() => {
    if (!seguir || l.carregando) return;
    if (l.proximo === ultimo.current || ++paginas.current > TETO_PAGINAS) { setParou(true); return; }
    ultimo.current = l.proximo; l.carregarMais();
  }, [seguir, l.carregando, l.itens]);
  const visiveis = filtroDia ? l.itens.filter((i) => i.data === filtroDia) : l.itens;
  return <View style={{ gap: 4 }}>
    {visiveis.map((i) => <LinhaLancamento key={i.id} item={i} comParcela aberto={aberto === i.id} alternar={() => setAberto(aberto === i.id ? null : i.id)} />)}
    {(l.carregando || seguir) && <ActivityIndicator accessibilityLabel="Carregando lançamentos" />}
    {l.erro && <><Texto tom="warning">{l.erro}</Texto><Acao rotulo="Tentar de novo" onPress={l.carregarMais} /></>}
    {parou && <Texto variante="legenda" tom="inkMuted">Pode haver mais lançamentos deste dia que não conseguimos carregar.</Texto>}
    {!l.carregando && !seguir && !parou && !l.erro && !visiveis.length && <Vazio texto="Nenhum lançamento encontrado." />}
    {l.proximo && !filtroDia && <Acao rotulo="Carregar mais lançamentos" desativado={l.carregando} onPress={l.carregarMais} />}
  </View>;
}
