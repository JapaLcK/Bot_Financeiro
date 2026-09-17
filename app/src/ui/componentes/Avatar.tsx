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
 * indistinguível de "sem foto" e pior que a inicial. A falha fica presa À
 * URI que falhou, então uma foto nova ainda é tentada.
 */
export function Avatar({ nome, imagem, tamanho = 40 }: Props) {
  const { cores } = useTema();
  // Guarda a URI que falhou, não um booleano: com booleano, trocar de foto
  // (outra conta, perfil atualizado) ou a linha ser reaproveitada numa lista
  // manteria as iniciais para sempre, porque o estado não volta.
  const [uriQueFalhou, setUriQueFalhou] = useState<string | null>(null);
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
      {imagem && uriQueFalhou !== imagem ? (
        <Image
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
