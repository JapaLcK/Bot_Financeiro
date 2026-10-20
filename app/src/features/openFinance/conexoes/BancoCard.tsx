import { Animated, Pressable, View } from "react-native";

import { corDoBanco } from "@/features/painel/composicaoContas";
import { Avatar } from "@/ui/componentes/Avatar";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Icone } from "@/ui/componentes/Icone";
import { Texto } from "@/ui/componentes/Texto";
import { usePressao } from "@/ui/motion";
import { claro, espaco, texto } from "@/ui/tokens";

import { logoDoBanco, type Item } from "./juntar";

interface Props {
  item: Item;
  /** Posição na lista: escolhe a tinta quando o banco não tem cor própria. */
  indice: number;
  aoAbrir: () => void;
}

/**
 * Uma linha do grupo (sem card próprio): logo, nome, estado e seta. NENHUM valor à mostra; tocar abre o detalhe do banco (`BancoDetalhe`, numa folha), onde moram total,
 * composição, contagens, há quanto tempo sincronizou e as ações. O nome acessível é "banco, estado": o valor só é falado depois de aberto.
 */
export function BancoCard({ item, indice, aoAbrir }: Props) {
  const { conexao: c } = item;
  const nome = c.institution_name ?? "Seu banco";
  const pressao = usePressao();
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${nome}, ${c.ui.label}`} onPress={aoAbrir} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar}>
      <Animated.View style={[{ flexDirection: "row", alignItems: "center", gap: espaco.md, minHeight: 56, paddingVertical: espaco.sm }, pressao.estilo]}>
        <Avatar nome={nome} imagem={logoDoBanco(c)} tamanho={44} decorativo fundo={claro.bg} letra={corDoBanco(nome, indice, claro)} />
        <View style={{ flex: 1, gap: espaco.xs }}>
          <Texto numberOfLines={1} style={{ fontSize: 16, lineHeight: 22, fontFamily: texto.secao.fontFamily }}>{nome}</Texto>
          <ConnectionStatus estado={c.ui.state} label={c.ui.label} />
        </View>
        <Icone nome="CaretRight" tamanho={20} tom="inkFaint" />
      </Animated.View>
    </Pressable>
  );
}
