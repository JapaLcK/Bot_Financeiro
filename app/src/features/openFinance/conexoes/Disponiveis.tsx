import { Animated, Pressable, View } from "react-native";

import type { Conector } from "@/api/schemas/openFinance";
import { corDoBanco } from "@/features/painel/composicaoContas";
import { Avatar } from "@/ui/componentes/Avatar";
import { Icone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Texto } from "@/ui/componentes/Texto";
import { usePressao } from "@/ui/motion";
import { claro, espaco, texto } from "@/ui/tokens";

import { logoSeguro } from "./juntar";

interface Props {
  /** Já filtrada por `disponiveis()` (populares ainda não conectados, por id). */
  conectores: Conector[];
  desativado: boolean;
  aoConectar: (id: number) => void;
  aoVerTodos: () => void;
}

function Linha({ c, i, desativado, aoConectar }: { c: Conector; i: number; desativado: boolean; aoConectar: (id: number) => void }) {
  const pressao = usePressao(); // a mesma resposta de toque do ListRow ("Ver todos")
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Conectar ${c.name}`} accessibilityState={{ disabled: desativado }} disabled={desativado} onPress={() => aoConectar(c.id)} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} style={{ opacity: desativado ? 0.45 : 1 }}>
      <Animated.View style={[{ flexDirection: "row", alignItems: "center", gap: espaco.md, minHeight: 56, paddingVertical: espaco.sm }, pressao.estilo]}>
        <Avatar nome={c.name} imagem={logoSeguro(c.logo)} tamanho={44} decorativo fundo={claro.bg} letra={corDoBanco(c.name, i, claro)} />
        <Texto numberOfLines={1} style={{ flex: 1, fontSize: 16, lineHeight: 22, fontFamily: texto.secao.fontFamily }}>{c.name}</Texto>
        <Icone nome="Plus" tamanho={20} tom="inkMuted" />
      </Animated.View>
    </Pressable>
  );
}

/** "Disponíveis para conectar": o "+" abre o fluxo de conexão com o banco pré-selecionado; "Ver todos" abre o mesmo fluxo sem escolha. A linha inteira é o botão. */
export function Disponiveis({ conectores, desativado, aoConectar, aoVerTodos }: Props) {
  return (
    <View style={{ gap: espaco.sm }}>
      <Texto variante="rotulo" tom="inkMuted" accessibilityRole="header">Disponíveis para conectar</Texto>
      <GrupoDeLinhas>
        {conectores.map((c, i) => <Linha key={c.id} c={c} i={i} desativado={desativado} aoConectar={aoConectar} />)}
        <ListRow titulo="Ver todos os bancos" chevron desativado={desativado} onPress={aoVerTodos} />
      </GrupoDeLinhas>
    </View>
  );
}
