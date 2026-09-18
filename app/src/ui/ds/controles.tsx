import { useState } from "react";
import { View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Chip } from "@/ui/componentes/Chip";
import { Input } from "@/ui/componentes/Input";
import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

const VARIANTES_BOTAO = ["primary", "secondary", "ghost", "danger"] as const;
const CATEGORIAS = ["Mercado", "Transporte", "Lazer"];

/**
 * Catálogo de Button, Chip, SegmentedControl e Input — os controles do C1
 * (fora de `app/_ds/index.tsx` por tamanho, CLAUDE.md §0.5, mesmo motivo de
 * `dinheiro.tsx`).
 */
export function SecaoControles() {
  const [periodo, setPeriodo] = useState("Semana");
  const [categoria, setCategoria] = useState<string | null>(null);
  const [erroNome, setErroNome] = useState(false);

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Controles</Texto>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Button
        </Texto>
        {VARIANTES_BOTAO.map((variante) => (
          <Button key={variante} rotulo={variante} variante={variante} onPress={() => {}} />
        ))}
        <Button rotulo="Desativado" onPress={() => {}} desativado />
        <Button rotulo="Carregando" onPress={() => {}} carregando />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Chip
        </Texto>
        <View style={{ flexDirection: "row", gap: espaco.sm }}>
          {CATEGORIAS.map((c) => (
            <Chip
              key={c}
              rotulo={c}
              selecionado={categoria === c}
              onPress={() => setCategoria((atual) => (atual === c ? null : c))}
            />
          ))}
        </View>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          SegmentedControl
        </Texto>
        <SegmentedControl opcoes={["Semana", "Mês", "Ano"]} valor={periodo} onChange={setPeriodo} />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Input
        </Texto>
        <Input
          rotulo="Nome"
          placeholder="Como te chamamos?"
          erro={erroNome ? "Preencha o nome" : undefined}
          onChangeText={(t) => setErroNome(t.length === 0)}
        />
        <Input rotulo="Desativado" desativado value="Não editável" />
      </View>
    </View>
  );
}
