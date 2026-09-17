import { useState } from "react";
import { Image, View } from "react-native";

import { useTema } from "@/ui/tema";

import { Texto } from "./Texto";

interface Props {
  nome: string;
  imagem?: string;
  tamanho?: number;
}

/**
 * Primeiro GRAFEMA visível, não o primeiro ponto de código. `Array.from(s)[0]`
 * resolve o par substituto ("😀"), mas ainda quebra em dois casos: um acento
 * DECOMPOSTO ("Élida" = E + U+0301 separado) perde a marca, e uma bandeira
 * ("🇧🇷" = dois indicadores regionais) sai cortada pela metade.
 *
 * `Intl.Segmenter` resolveria os dois, mas o app roda em Hermes, e não há como
 * provar aqui que esta versão o implementa (não há vestígio dele no hermes
 * vendorizado por este react-native, e o Jest roda em Node — que TEM
 * Segmenter e mascararia a ausência). Por isso: regex com propriedades
 * Unicode (base + marcas combinantes, e par de indicadores regionais à
 * parte para bandeira). `@react-native/babel-preset` sempre inclui
 * `plugin-transform-unicode-regex`, que compila `\p{...}` num equivalente que
 * não depende de suporte nativo do motor — funciona em Hermes e em Node.
 *
 * ponytail: não cobre sequência ZWJ (emoji de família, "👨‍👩‍👧" ainda sai
 * como "👨" solto) — exigiria um regex de emoji completo, e Avatar recebe
 * nome de pessoa, não emoji de família como nome.
 */
const REGRA_GRAFEMA = /^(?:\p{Regional_Indicator}\p{Regional_Indicator}|\P{M}\p{M}*)/u;

function primeiraLetra(s: string): string {
  return REGRA_GRAFEMA.exec(s)?.[0] ?? "";
}

/** Primeira letra do primeiro nome + primeira do último (duas+ palavras); uma palavra só, uma letra; vazio, sem iniciais. */
function iniciais(nome: string): string {
  const partes = nome.trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return "";
  const primeira = primeiraLetra(partes[0] ?? "");
  const ultima = partes.length > 1 ? primeiraLetra(partes[partes.length - 1] ?? "") : "";
  return (primeira + ultima).toLocaleUpperCase("pt-BR");
}

/**
 * Neutro por padrão (fundo `surface`, texto `inkMuted`): nunca um avatar
 * "quadrado colorido" por pessoa (identidade pigbank-frontend) — a cor não
 * carrega significado aqui. Nome vazio ou sem letra cai no fallback "?" em
 * vez de um círculo em branco, que pareceria erro de carregamento.
 *
 * Imagem que FALHA cai no mesmo fallback: a foto vem de fora (provedor de
 * conta, banco), e uma URL inalcançável deixaria o círculo vazio — que é
 * indistinguível de "sem foto" e pior que a inicial. A falha vale só enquanto
 * a `imagem` for a mesma: qualquer troca de URI tenta de novo.
 */
export function Avatar({ nome, imagem, tamanho = 40 }: Props) {
  const { cores } = useTema();
  // A falha vale só para a URI ATUAL: zera a cada troca de `imagem`. Guardar
  // um booleano manteria as iniciais para sempre; guardar só "qual URI falhou"
  // ainda prenderia o ciclo A → B → A (linha reaproveitada numa lista longa),
  // que nunca tentaria A de novo mesmo que a falha tivesse sido passageira.
  const [uriQueFalhou, setUriQueFalhou] = useState<string | null>(null);
  const [uriVista, setUriVista] = useState(imagem);
  if (imagem !== uriVista) {
    // Ajuste de estado durante o render (padrão do React para estado derivado
    // de prop): sem isto, o reset só aconteceria depois de um render extra
    // mostrando as iniciais da foto anterior.
    setUriVista(imagem);
    setUriQueFalhou(null);
  }
  const letras = iniciais(nome);
  const rotulo = nome.trim() || "Sem nome";

  return (
    <View
      accessible
      accessibilityLabel={rotulo}
      style={{
        width: tamanho,
        height: tamanho,
        borderRadius: tamanho / 2,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: cores.surface,
        overflow: "hidden",
      }}
    >
      {imagem && uriQueFalhou === null ? (
        <Image
          // `key` por URI: cada foto é um elemento próprio, então o erro
          // atrasado de uma imagem que já saiu da tela não chega ao handler da
          // imagem nova (sem isso, trocar A por B enquanto A ainda carregava
          // deixaria o erro de A marcar B como falha).
          key={imagem}
          source={{ uri: imagem }}
          style={{ width: tamanho, height: tamanho }}
          onError={() => setUriQueFalhou(imagem)}
          accessibilityIgnoresInvertColors
        />
      ) : (
        <Texto variante="rotulo" tom="inkMuted" style={{ fontSize: tamanho * 0.4, lineHeight: tamanho * 0.5 }}>
          {letras || "?"}
        </Texto>
      )}
    </View>
  );
}
