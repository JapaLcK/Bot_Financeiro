import { Image, View } from "react-native";

import { STICKERS, type NomeSticker } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

import { Button } from "./Button";
import { Texto } from "./Texto";

interface Acao {
  rotulo: string;
  onPress: () => void;
}

interface Props {
  sticker: NomeSticker;
  frase: string;
  acao?: Acao;
}

const TAMANHO_STICKER = 96;
const TAMANHO_HALO = TAMANHO_STICKER + espaco.xl * 2;

/**
 * Halo só no escuro: os stickers são desenhados para fundo claro, e sem um
 * disco `surface` atrás deles no tema escuro o recorte do webp fica sem
 * contexto (não é decoração à toa — é o mesmo motivo do `Card` não usar
 * sombra no escuro, `surfaceRaised` já contrasta sozinho ali; aqui é o
 * oposto, falta contraste sem o halo).
 */
export function EmptyState({ sticker, frase, acao }: Props) {
  const { esquema, cores } = useTema();

  return (
    <View style={{ alignItems: "center", gap: espaco.lg, padding: espaco.xxl }}>
      <View
        style={
          esquema === "dark"
            ? {
                width: TAMANHO_HALO,
                height: TAMANHO_HALO,
                borderRadius: TAMANHO_HALO / 2,
                backgroundColor: cores.surface,
                alignItems: "center",
                justifyContent: "center",
              }
            : undefined
        }
      >
        <Image
          source={STICKERS[sticker]}
          accessibilityIgnoresInvertColors
          resizeMode="contain"
          style={{ width: TAMANHO_STICKER, height: TAMANHO_STICKER }}
        />
      </View>
      <Texto variante="corpo" tom="inkMuted" style={{ textAlign: "center" }}>
        {frase}
      </Texto>
      {acao ? <Button rotulo={acao.rotulo} onPress={acao.onPress} /> : null}
    </View>
  );
}
