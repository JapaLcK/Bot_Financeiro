import { View } from "react-native";

import { faladoComSinal } from "@/ui/dinheiro";

import { ListRow } from "./ListRow";
import { Money } from "./Money";

type Tipo = "entrada" | "saida";

interface Props {
  descricao: string;
  categoria: string;
  /** Já formatada pelo chamador (ex.: "Hoje, 14:32") — formatação de data não é escopo deste componente. */
  data?: string;
  centavos: number;
  tipo: Tipo;
  /** Repassada ao `Money`: máscara "R$ ••••" sem nenhum dígito na árvore (decisão do dono, PR B). */
  oculto?: boolean;
}

/**
 * `ListRow` + `Money` (CLAUDE.md §0.1: não reimplementa a casca de nenhum
 * dos dois). "•" (U+2022) separa categoria e data — mesmo glifo que a
 * máscara de valor oculto do `Money` já exige da fonte
 * (`tests/test_app_espelhos.py`), não um caractere novo para o app garantir.
 *
 * A11y NUM rótulo: o `View` externo marcado `accessible` vira o único parada
 * de foco do leitor de tela (RN colapsa a subárvore inteira nele), então o
 * `accessibilityLabel` do `Money` interno não some — só nunca é lido
 * separadamente da linha. `faladoComSinal` é a MESMA função que `Money` usa
 * para o próprio rótulo (CLAUDE.md §0.7): remontar "sinal + falado" aqui
 * separado já tinha divergido em zero ("menos zero reais") e valor inválido
 * ("mais valor indisponível").
 */
export function TransactionRow({ descricao, categoria, data, centavos, tipo, oculto = false }: Props) {
  const valorFalado = oculto ? "valor oculto" : faladoComSinal(centavos, tipo);
  const rotulo = [descricao, categoria, data, valorFalado].filter(Boolean).join(", ");

  return (
    <View accessible accessibilityLabel={rotulo}>
      <ListRow
        titulo={descricao}
        subtitulo={data ? `${categoria} • ${data}` : categoria}
        trailing={<Money centavos={centavos} tipo={tipo} variante="corpo" oculto={oculto} />}
      />
    </View>
  );
}
