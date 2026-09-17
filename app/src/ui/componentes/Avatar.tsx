import { Image, View } from "react-native";

import { useTema } from "@/ui/tema";

import { Texto } from "./Texto";

interface Props {
  nome: string;
  imagem?: string;
  tamanho?: number;
}

/**
 * `[0]` indexa por unidade UTF-16: um emoji fora do plano básico (ex.: "😀")
 * é um par substituto, e `[0]` pega só a METADE dele — um caractere solto que
 * quebra a exibição. `Array.from(s)[0]` itera por PONTO DE CÓDIGO, então o
 * par sempre sai inteiro.
 */
function primeiraLetra(s: string): string {
  return Array.from(s)[0] ?? "";
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
 */
export function Avatar({ nome, imagem, tamanho = 40 }: Props) {
  const { cores } = useTema();
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
      {imagem ? (
        <Image source={{ uri: imagem }} style={{ width: tamanho, height: tamanho }} accessibilityIgnoresInvertColors />
      ) : (
        <Texto variante="rotulo" tom="inkMuted" style={{ fontSize: tamanho * 0.4, lineHeight: tamanho * 0.5 }}>
          {letras || "?"}
        </Texto>
      )}
    </View>
  );
}
