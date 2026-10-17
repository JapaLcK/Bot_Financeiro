import { useEffect, useRef, useState, type ReactNode } from "react";
import { Animated, Pressable, View } from "react-native";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { selecao } from "@/ui/haptics";
import { duracoes, facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio, tingir } from "@/ui/tokens";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

/** `decisao` (rosa): pede uma resposta do usuário. `alerta` (âmbar): os números ou a conexão estão com problema. Só apresentação: a pilha não sabe de banco nem de receita. */
export type AvisoDaPilha = { id: string; tom: "decisao" | "alerta"; icone: NomeIcone; titulo: string; corpo: ReactNode; aoFechar?: () => void };
// `fio` = borda e fundo tingido; `tinta` = ícone e pontos. Nunca `brandSoft` de fundo: `brand` sobre `brandSoft` é o par proibido do `contraste.test.ts`.
const TOM = { decisao: { fio: "brand", tinta: "brandInk" }, alerta: { fio: "warning", tinta: "warning" } } as const;

/**
 * Um aviso por vez, com a borda do próximo por baixo e pontos "1 de N". Trocar de aviso é por toque nos pontos (alvo de 44pt) ou pelo VoiceOver:
 * a linha de pontos é UM elemento "adjustable" (deslizar para cima/baixo troca e o leitor lê o valor novo). Não desliza na horizontal: brigaria com a rolagem e com o voltar do iOS.
 * A seleção é o ID, não o índice: quando o visível some (receita respondida, aviso fechado) cai no primeiro, sem índice fora da lista.
 * Não anuncia nada ao montar (aviso que já nasce na tela não interrompe a leitura, como o `Banner`).
 */
export function PilhaDeAvisos({ itens }: { itens: AvisoDaPilha[] }) {
  const { cores, esquema, acesso } = useTema(); const { ampliado } = useTipografiaPainel(); const reduzir = useReduzirMovimento();
  const [selecionado, setSelecionado] = useState<string | null>(null);
  const subida = useRef(new Animated.Value(1)).current; const vista = useRef<string | undefined>(undefined);
  const n = itens.length, i = Math.max(0, itens.findIndex((x) => x.id === selecionado)), atual = itens[i], proximo = itens[(i + 1) % n], vid = atual?.id;
  // Seleção morta (o aviso sumiu): volta a null = "o primeiro", sem gesto não há seleção. Prender no id visível deixaria a receita (que chegou antes) na frente do banco, que tem prioridade; e o id antigo, se voltar (Desfazer), não ressuscita.
  // ponytail: lista vazia = sumiço transitório (puxar para atualizar zera `dados`), mantém a escolha. Com a conexão pendente na pilha (não vem de `dados`) a lista não esvazia e a escolha cai no primeiro; aceito.
  if (selecionado !== null && itens.length > 0 && !itens.some((x) => x.id === selecionado)) setSelecionado(null);
  useEffect(() => {
    const antes = vista.current; vista.current = vid;
    if (vid === undefined || antes === undefined || antes === vid || reduzir) return;
    subida.setValue(0); Animated.timing(subida, { toValue: 1, duration: duracoes.transicao, easing: facilitador, useNativeDriver: true }).start();
  }, [vid, reduzir, subida]);
  if (!atual || !proximo) return null;
  const ir = (k: number) => { const alvo = itens[Math.min(n - 1, Math.max(0, k))]; if (alvo && alvo.id !== atual.id) { selecao(); setSelecionado(alvo.id); } };
  const t = TOM[atual.tom], fio = cores[t.fio], rotulo = n > 1 ? `Aviso ${i + 1} de ${n}: ${atual.titulo}` : atual.titulo;
  return <View testID="pilha-avisos">
    <Animated.View style={{ opacity: subida, transform: [{ translateY: subida.interpolate({ inputRange: [0, 1], outputRange: [8, 0] }) }], flexDirection: "row", gap: 12, padding: 16, borderRadius: raio.bloco, borderWidth: 1, borderColor: fio, backgroundColor: acesso ? cores.bg : tingir(fio, esquema) }}>
      <Icone nome={atual.icone} tom={t.tinta} />
      <View style={{ flex: 1, minWidth: 0, gap: 8 }}>
        <Texto variante="rotulo" accessibilityRole="header" accessibilityLabel={rotulo}>{atual.titulo}</Texto>
        {atual.corpo}
      </View>
      {atual.aoFechar && <Pressable accessibilityRole="button" accessibilityLabel="Fechar aviso" onPress={atual.aoFechar} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center", alignSelf: "flex-start", margin: -12 }}><Icone nome="X" tamanho={20} tom="inkMuted" /></Pressable>}
    </Animated.View>
    {n > 1 && <>
      {/* A borda do próximo por baixo: só pista visual, o leitor de tela fica com os pontos. */}
      <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ height: 6, marginHorizontal: 8, borderBottomLeftRadius: raio.bloco, borderBottomRightRadius: raio.bloco, borderWidth: 1, borderTopWidth: 0, borderColor: cores[TOM[proximo.tom].fio] }} />
      <View accessible accessibilityRole="adjustable" accessibilityLabel="Avisos" accessibilityValue={{ text: rotulo }} accessibilityActions={[{ name: "increment" }, { name: "decrement" }]} onAccessibilityAction={(e) => ir(i + (e.nativeEvent.actionName === "increment" ? 1 : -1))} style={{ flexDirection: "row", flexWrap: "wrap", justifyContent: "center", alignItems: "center", alignSelf: ampliado ? "flex-start" : "center" }}>
        {itens.map((x, k) => <Pressable key={x.id} testID={`pilha-ponto-${k}`} onPress={() => ir(k)} style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}><View style={{ width: k === i ? 10 : 8, height: k === i ? 10 : 8, borderRadius: raio.pilula, backgroundColor: cores[TOM[x.tom].tinta], opacity: k === i ? 1 : 0.4 }} /></Pressable>)}
      </View>
    </>}
  </View>;
}
