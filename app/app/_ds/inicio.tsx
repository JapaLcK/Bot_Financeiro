import { View } from "react-native";

import { Banner } from "@/ui/componentes/Banner";
import { Card } from "@/ui/componentes/Card";
import { InsightCard } from "@/ui/componentes/InsightCard";
import { Money } from "@/ui/componentes/Money";
import { ProgressBar } from "@/ui/componentes/ProgressBar";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { TransactionRow } from "@/ui/componentes/TransactionRow";
import { CONEXAO_FALSA, TRANSACOES_FALSAS } from "@/ui/ds/dados";
import { espaco } from "@/ui/tokens";

/**
 * Tela-modelo (dado falso, `@/ui/ds/dados`): prova o design system numa
 * composição real, não só peça a peça no catálogo. É a que o dono/orquestrador
 * confere com Dynamic Type grande e nos dois temas.
 */
export default function TelaInicio() {
  return (
    <Screen>
      <View style={{ gap: espaco.lg, paddingTop: espaco.lg, paddingBottom: espaco.xxl }}>
        <Texto variante="titulo">Olá, Ana</Texto>

        <Card elevacao="raised">
          <Texto variante="rotulo" tom="inkMuted">
            Saldo
          </Texto>
          <Money centavos={123456789} variante="display" />
        </Card>

        <Card elevacao="raised">
          <Texto variante="rotulo" tom="inkMuted">
            Gasto do mês
          </Texto>
          <ProgressBar valor={0.62} tom="warning" />
        </Card>

        <Banner tom="warning" titulo="Conexão" mensagem={`${CONEXAO_FALSA.label} — ${CONEXAO_FALSA.detalhe}`} />

        <Card elevacao="raised">
          {TRANSACOES_FALSAS.map((t, indice) => (
            <TransactionRow key={t.descricao + indice} {...t} />
          ))}
        </Card>

        <InsightCard
          sticker="goal"
          titulo="Você está perto da meta"
          mensagem="Faltam R$ 120,00 para o mês fechar dentro do previsto."
          acao={{ rotulo: "Ver detalhes", onPress: () => {} }}
        />
      </View>
    </Screen>
  );
}
