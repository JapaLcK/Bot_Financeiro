import { useState } from "react";
import { View } from "react-native";
import { Acao, Avisos, Vazio } from "./base";
import { useBloco } from "./blocoContexto";
import { LinhaLancamento } from "./extrato";
// Os cards `ultimos_lancamentos` e `sem_categoria` que a PRÓPRIA tool do servidor emite, só no chat: a mesma linha do Extrato (`LinhaLancamento`), desenhando SÓ o bloco.
const VISIVEIS = 5; // lançamentos visíveis antes do "+ N lançamentos"
export const TITULO_LANCAMENTOS = { ultimos_lancamentos: "Últimos lançamentos", sem_categoria: "Sem categoria" } as const;
/**
 * Até 10 lançamentos, 5 à vista e "+ N" que abre o resto na própria lista (sem folha). Tocar na linha só abre o detalhe dela aqui mesmo (a `LinhaLancamento` não navega):
 * o lançamento de fatura do mês seguinte (`fatura` futura) não leva ao Extrato daquele mês, que a rota recusa com 422. Serve a uma lista de 1 item (a confirmação de apagar).
 */
export function CardLancamentos({ id }: { id: keyof typeof TITULO_LANCAMENTOS }) {
  const d = useBloco()?.recursos[id]; const [todas, setTodas] = useState(false); const [aberto, setAberto] = useState<string | null>(null);
  if (!d) return <Vazio texto="Dado indisponível neste card." />;
  const resto = d.itens.length - VISIVEIS;
  return <View style={{ gap: 12 }}>
    {!d.itens.length ? <Vazio texto="Nenhum lançamento." /> : <View>
      {(todas ? d.itens : d.itens.slice(0, VISIVEIS)).map((i) => <LinhaLancamento key={i.id} item={i} comParcela aberto={aberto === i.id} alternar={() => setAberto(aberto === i.id ? null : i.id)} />)}
      {resto > 0 && <Acao rotulo={todas ? "Mostrar menos" : `+ ${resto} ${resto === 1 ? "lançamento" : "lançamentos"}`} onPress={() => setTodas((t) => !t)} />}
    </View>}
    <Avisos motivos={d.motivos} />
  </View>;
}
