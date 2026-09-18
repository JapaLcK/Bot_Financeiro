import { View } from "react-native";

import { Banner } from "@/ui/componentes/Banner";
import { InsightCard } from "@/ui/componentes/InsightCard";
import { Texto } from "@/ui/componentes/Texto";
import { TransactionRow } from "@/ui/componentes/TransactionRow";
import { TRANSACOES_FALSAS } from "@/ui/ds/dados";
import { espaco } from "@/ui/tokens";

/**
 * Catálogo de `Banner`, `TransactionRow` e `InsightCard` (PR C2) — fora de
 * `app/_ds/index.tsx` por tamanho, mesmo motivo de `controles.tsx`/
 * `exibicao.tsx`/`dinheiro.tsx` (CLAUDE.md §0.5).
 */
export function SecaoComposicao() {
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Composição</Texto>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Banner
        </Texto>
        <Banner tom="info" mensagem="Sua fatura fecha em 3 dias." />
        <Banner tom="warning" titulo="Conexão parcial" mensagem="Alguns lançamentos podem estar desatualizados." />
        <Banner
          tom="danger"
          titulo="Conexão perdida"
          mensagem="Reconecte para continuar recebendo lançamentos."
          acao={{ rotulo: "Reconectar", onPress: () => {} }}
        />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          TransactionRow
        </Texto>
        {TRANSACOES_FALSAS.map((t, indice) => (
          <TransactionRow key={t.descricao + indice} {...t} />
        ))}
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          InsightCard
        </Texto>
        <InsightCard
          sticker="goal"
          titulo="Você está perto da meta"
          mensagem="Faltam R$ 120,00 para o mês fechar dentro do previsto."
          acao={{ rotulo: "Ver detalhes", onPress: () => {} }}
        />
        <InsightCard titulo="Sem sticker" mensagem="InsightCard também funciona sem imagem." />
      </View>
    </View>
  );
}
