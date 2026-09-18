import { useState } from "react";
import { Pressable, View } from "react-native";

import { Card } from "@/ui/componentes/Card";
import { Chip } from "@/ui/componentes/Chip";
import { EmptyState } from "@/ui/componentes/EmptyState";
import { ListRow } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { Skeleton } from "@/ui/componentes/Skeleton";
import { Texto } from "@/ui/componentes/Texto";
import { CATEGORIAS_FALSAS, ITENS_LISTA_FALSOS } from "@/ui/ds/dados";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

/**
 * Tela-modelo (dado falso): filtrar por "Lazer" mostra o `EmptyState` de
 * propósito — não existe item dessa categoria em `ITENS_LISTA_FALSOS`.
 */
export default function TelaLista() {
  const { cores } = useTema();
  const [periodo, setPeriodo] = useState("Mês");
  const [categoria, setCategoria] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(false);

  const itens = categoria ? ITENS_LISTA_FALSOS.filter((i) => i.categoria === categoria) : ITENS_LISTA_FALSOS;

  return (
    <Screen>
      <View style={{ gap: espaco.lg, paddingTop: espaco.lg, paddingBottom: espaco.xxl }}>
        <Texto variante="titulo">Lançamentos</Texto>

        <SegmentedControl opcoes={["Semana", "Mês", "Ano"]} valor={periodo} onChange={setPeriodo} />

        <View style={{ flexDirection: "row", gap: espaco.sm, flexWrap: "wrap" }}>
          {CATEGORIAS_FALSAS.map((c) => (
            <Chip
              key={c}
              rotulo={c}
              selecionado={categoria === c}
              onPress={() => setCategoria((atual) => (atual === c ? null : c))}
            />
          ))}
        </View>

        <Pressable
          accessibilityRole="button"
          onPress={() => setCarregando((v) => !v)}
          style={{
            alignSelf: "flex-start",
            minHeight: 44,
            justifyContent: "center",
            paddingHorizontal: espaco.lg,
            borderRadius: raio.lg,
            backgroundColor: cores.brandSoft,
          }}
        >
          <Texto variante="rotulo" tom="brandInk">
            {carregando ? "Parar de carregar" : "Simular carregando"}
          </Texto>
        </Pressable>

        <Card elevacao="raised">
          {carregando ? (
            <View style={{ gap: espaco.md }}>
              <Skeleton largura={220} altura={20} />
              <Skeleton largura={160} altura={16} />
              <Skeleton largura={200} altura={16} />
            </View>
          ) : itens.length === 0 ? (
            <EmptyState
              sticker="thinking"
              frase="Nada por aqui com esse filtro."
              acao={{ rotulo: "Limpar filtro", onPress: () => setCategoria(null) }}
            />
          ) : (
            itens.map((item, indice) => (
              <ListRow key={item.titulo + indice} titulo={item.titulo} subtitulo={item.subtitulo} chevron onPress={() => {}} />
            ))
          )}
        </Card>
      </View>
    </Screen>
  );
}
