import { Link } from "expo-router";
import { useState } from "react";
import { Pressable, useColorScheme, View } from "react-native";

import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { TemaProvider, useTema } from "@/ui/tema";
import { claro, espaco, raio, texto as escalas, type Paleta } from "@/ui/tokens";

// `require()`, não `import`, para as cinco seções: um `import` estático faz
// este módulo puxar os componentes do design system e os ícones reais do
// Phosphor assim que ALGUÉM requer `_ds/index.tsx` — inclusive o roteador,
// que avalia o arquivo de toda rota para montar a tabela de rotas mesmo com
// o `Stack.Protected` guardando o acesso (`app/_layout.tsx`), mesmo em
// produção onde a tela nunca abre. Com o require adiado, esse custo só é
// pago quando o catálogo é DE FATO montado.
// O que motivou a mudança foi uma suspeita de lentidão em `layout.test.tsx`
// (o teste que prova a guarda) que NÃO se sustentou ao remedir: as duas
// formas deram tempos equivalentes, e os 5–18s vistos uma vez foram carga da
// máquina, não este import. O require fica porque é mais barato de qualquer
// forma; se alguém preferir o import estático, não vai encontrar regressão
// de teste por isso.
type SecaoDinheiroModulo = typeof import("@/ui/ds/dinheiro");
type SecaoControlesModulo = typeof import("@/ui/ds/controles");
type SecaoExibicaoModulo = typeof import("@/ui/ds/exibicao");
type SecaoComposicaoModulo = typeof import("@/ui/ds/composicao");
type SecaoEstadoModulo = typeof import("@/ui/ds/estado");

type Esquema = "light" | "dark";

const NOMES_COR = Object.keys(claro) as (keyof Paleta)[];
const VARIANTES_TEXTO = Object.keys(escalas) as (keyof typeof escalas)[];

/**
 * Catálogo do design system, só em dev (as duas guardas ficam em `_layout.tsx`
 * — aqui e na raiz). Cobre o que a Fundação entrega: a tabela de cor completa
 * e as seis variantes tipográficas, nos dois temas.
 */
export default function DsIndex() {
  // Começa no tema do aparelho; o botão só troca para conferir o outro.
  const sistema = useColorScheme();
  const [esquema, setEsquema] = useState<Esquema>(sistema === "dark" ? "dark" : "light");
  return (
    <TemaProvider esquema={esquema}>
      <Catalogo esquema={esquema} onTrocar={() => setEsquema((e) => (e === "light" ? "dark" : "light"))} />
    </TemaProvider>
  );
}

function Catalogo(props: { esquema: Esquema; onTrocar: () => void }) {
  const { cores } = useTema();
  const { SecaoDinheiro } = require("@/ui/ds/dinheiro") as SecaoDinheiroModulo;
  const { SecaoControles } = require("@/ui/ds/controles") as SecaoControlesModulo;
  const { SecaoExibicao } = require("@/ui/ds/exibicao") as SecaoExibicaoModulo;
  const { SecaoComposicao } = require("@/ui/ds/composicao") as SecaoComposicaoModulo;
  const { SecaoEstado } = require("@/ui/ds/estado") as SecaoEstadoModulo;
  return (
    <Screen>
      {/*
       * Respiro do CATÁLOGO, não do `Screen`: antes de adotar o `Screen` do
       * C1, esta tela tinha padding próprio (insets + `espaco.lg`/`xxl`
       * vertical, `espaco.xxl` horizontal — ver histórico do PR A). O
       * `Screen` compartilhado só dá `espaco.lg` (16) horizontal e os
       * insets crus verticalmente — menos do que este catálogo tinha. O
       * extra some AQUI (não no `Screen`, que é usado por outras telas sem
       * pedir o mesmo respiro).
       */}
      <View style={{ gap: espaco.lg, paddingTop: espaco.lg, paddingBottom: espaco.xxl, paddingHorizontal: espaco.sm }}>
        <Pressable
          accessibilityRole="button"
          onPress={props.onTrocar}
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
            Ver em {props.esquema === "light" ? "escuro" : "claro"}
          </Texto>
        </Pressable>

        <Texto variante="secao">Cor</Texto>
        {NOMES_COR.map((nome) => (
          <View key={nome} style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
            <View
              style={{
                width: 32,
                height: 32,
                borderRadius: raio.sm,
                backgroundColor: cores[nome],
                borderWidth: 1,
                borderColor: cores.border,
              }}
            />
            <Texto variante="legenda" tom="inkMuted">
              {nome} · {cores[nome]}
            </Texto>
          </View>
        ))}

        <Texto variante="secao">Tipografia</Texto>
        {VARIANTES_TEXTO.map((variante) => (
          <Texto key={variante} variante={variante}>
            {variante}
          </Texto>
        ))}

        <SecaoDinheiro />
        <SecaoControles />
        <SecaoExibicao />
        <SecaoComposicao />
        <SecaoEstado />

        <Texto variante="secao">Telas</Texto>
        <View style={{ gap: espaco.sm }}>
          {(["inicio", "lista", "formulario", "sheet-exemplo"] as const).map((rota) => (
            <Link key={rota} href={`/_ds/${rota}`} asChild>
              <Pressable
                accessibilityRole="button"
                style={{
                  minHeight: 44,
                  justifyContent: "center",
                  paddingHorizontal: espaco.lg,
                  borderRadius: raio.md,
                  backgroundColor: cores.surface,
                }}
              >
                <Texto variante="rotulo" tom="ink">
                  {rota}
                </Texto>
              </Pressable>
            </Link>
          ))}
        </View>
      </View>
    </Screen>
  );
}
