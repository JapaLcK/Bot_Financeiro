import { View } from "react-native";

import { Avatar } from "@/ui/componentes/Avatar";
import { Card } from "@/ui/componentes/Card";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { ListRow } from "@/ui/componentes/ListRow";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { Skeleton } from "@/ui/componentes/Skeleton";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

const ICONES: NomeIcone[] = ["CaretRight", "Wallet", "Bell"];

/**
 * Catálogo de Card, ListRow, ProgressBar, Skeleton, Avatar e Icone — os
 * componentes de EXIBIÇÃO do C1 (fora de `app/_ds/index.tsx` por tamanho,
 * mesmo motivo de `controles.tsx`/`dinheiro.tsx`, CLAUDE.md §0.5).
 */
export function SecaoExibicao() {
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Exibição</Texto>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Card
        </Texto>
        <Card elevacao="surface">
          <Texto>surface</Texto>
        </Card>
        <Card elevacao="raised">
          <Texto>raised</Texto>
        </Card>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          ListRow
        </Texto>
        <Card elevacao="raised">
          <ListRow icone="Wallet" titulo="Mercado" subtitulo="Hoje, 14:32" chevron onPress={() => {}} />
          <ListRow icone="Bell" titulo="Alertas" subtitulo="3 novos" chevron onPress={() => {}} />
        </Card>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          ProgressBar
        </Texto>
        <ProgressBar valor={0.3} />
        <ProgressBar valor={0.7} tom="positive" />
        <ProgressBar valor={0.9} tom="warning" />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Skeleton
        </Texto>
        <Skeleton largura={200} altura={16} />
        <Skeleton largura={120} altura={16} />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Avatar
        </Texto>
        <View style={{ flexDirection: "row", gap: espaco.sm }}>
          <Avatar nome="Ana Clara Souza" />
          <Avatar nome="Élida" />
          <Avatar nome="" />
        </View>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Icone
        </Texto>
        <View style={{ flexDirection: "row", gap: espaco.lg }}>
          {ICONES.map((nome) => (
            <Icone key={nome} nome={nome} />
          ))}
        </View>
      </View>
    </View>
  );
}
