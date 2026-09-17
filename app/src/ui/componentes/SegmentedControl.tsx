import { useEffect, useRef } from "react";
import { Animated, Pressable, View } from "react-native";

import { selecao } from "@/ui/haptics";
import { duracoes, facilitador, usePressao } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

import { Texto } from "./Texto";

interface Props {
  opcoes: string[];
  valor: string;
  onChange: (v: string) => void;
}

/**
 * `selecao()` só na TROCA (nunca ao tocar a opção já ativa — mesma regra do
 * `useAvisoAoErrar`: sinal tátil é para transição, não para repetição). O
 * destaque crossfada 150ms (`duracoes.feedback`) em vez de deslizar: sem
 * `reanimated` nesta fase (decisão do dono), opacidade é a única transição
 * barata com `Animated` nativo entre N opções dinâmicas.
 *
 * Estado indexado por POSIÇÃO, nunca pelo rótulo: com dois rótulos iguais
 * (`["Mês", "Mês"]`), uma `Map<string, Animated.Value>` chaveada por texto
 * colide — os dois compartilham UM valor animado, `key={opcao}` duplica no
 * React, e `opcao === valor` marca os DOIS como selecionados ao mesmo tempo,
 * deixando o segundo morto (tocar nele não muda nada porque já "parece"
 * ativo). O índice do selecionado vem de `opcoes.indexOf(valor)` (o primeiro
 * que bate) — com rótulos únicos isso é exatamente o de sempre.
 */
export function SegmentedControl({ opcoes, valor, onChange }: Props) {
  const { cores } = useTema();
  const indiceSelecionado = opcoes.indexOf(valor);
  const opacidades = useRef<Animated.Value[]>([]).current;
  opcoes.forEach((_, indice) => {
    if (!opacidades[indice]) opacidades[indice] = new Animated.Value(indice === indiceSelecionado ? 1 : 0);
  });

  useEffect(() => {
    opcoes.forEach((_, indice) => {
      Animated.timing(opacidades[indice]!, {
        toValue: indice === indiceSelecionado ? 1 : 0,
        duration: duracoes.feedback,
        easing: facilitador,
        useNativeDriver: true,
      }).start();
    });
  }, [indiceSelecionado, opcoes, opacidades]);

  function selecionar(indice: number) {
    if (indice === indiceSelecionado) return;
    selecao();
    onChange(opcoes[indice]!);
  }

  return (
    <View
      accessibilityRole="tablist"
      style={{ flexDirection: "row", backgroundColor: cores.surface, borderRadius: raio.md, padding: espaco.xs }}
    >
      {opcoes.map((opcao, indice) => (
        <Segmento
          key={indice}
          opcao={opcao}
          selecionado={indice === indiceSelecionado}
          opacidade={opacidades[indice]!}
          onPress={() => selecionar(indice)}
        />
      ))}
    </View>
  );
}

function Segmento({
  opcao,
  selecionado,
  opacidade,
  onPress,
}: {
  opcao: string;
  selecionado: boolean;
  opacidade: Animated.Value;
  onPress: () => void;
}) {
  const { cores } = useTema();
  const pressao = usePressao();

  return (
    <Pressable
      onPress={onPress}
      onPressIn={pressao.aoPressionar}
      onPressOut={pressao.aoSoltar}
      accessibilityRole="tab"
      accessibilityState={{ selected: selecionado }}
      style={{ flex: 1, minHeight: 44 }}
    >
      <Animated.View style={[{ flex: 1 }, pressao.estilo]}>
        <Animated.View
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            borderRadius: raio.sm,
            backgroundColor: cores.surfaceRaised,
            // A distinção NÃO pode depender só do fundo: `surfaceRaised` sobre
            // `surface` mede 1,08 (claro) e 1,10 (escuro) — abaixo de qualquer
            // teto de contraste, ou seja, invisível. A borda de 1px `inkMuted`
            // (3:1, já em `PARES`) é o que de fato marca o selecionado; sem
            // ela o indicador de fundo sozinho não se vê nos dois temas.
            borderWidth: selecionado ? 1 : 0,
            borderColor: cores.inkMuted,
            opacity: opacidade,
          }}
        />
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <Texto variante="rotulo" tom={selecionado ? "ink" : "inkMuted"}>
            {opcao}
          </Texto>
        </View>
      </Animated.View>
    </Pressable>
  );
}
