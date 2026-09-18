import { Image, View } from "react-native";

import { STICKERS, type NomeSticker } from "@/ui/stickers";
import { espaco } from "@/ui/tokens";

import { Button } from "./Button";
import { Card } from "./Card";
import { Texto } from "./Texto";

interface Acao {
  rotulo: string;
  onPress: () => void;
}

interface Props {
  sticker?: NomeSticker;
  titulo: string;
  mensagem: string;
  acao?: Acao;
}

const TAMANHO_STICKER = 40;

/** `Card` + `Texto` (CLAUDE.md §0.1) — a prop de elevação do `Card` é `elevacao`, não `tone`. */
export function InsightCard({ sticker, titulo, mensagem, acao }: Props) {
  return (
    <Card elevacao="raised">
      <View style={{ flexDirection: "row", gap: espaco.md }}>
        {sticker ? (
          <Image
            source={STICKERS[sticker]}
            accessibilityIgnoresInvertColors
            resizeMode="contain"
            style={{ width: TAMANHO_STICKER, height: TAMANHO_STICKER }}
          />
        ) : null}
        <View style={{ flex: 1, gap: espaco.xs }}>
          <Texto variante="rotulo" tom="ink">
            {titulo}
          </Texto>
          <Texto variante="corpo" tom="inkMuted">
            {mensagem}
          </Texto>
          {acao ? <Button rotulo={acao.rotulo} variante="ghost" tamanho="M" onPress={acao.onPress} /> : null}
        </View>
      </View>
    </Card>
  );
}
