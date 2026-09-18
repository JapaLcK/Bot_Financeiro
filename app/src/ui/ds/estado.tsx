import { View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { EmptyState } from "@/ui/componentes/EmptyState";
import { Texto } from "@/ui/componentes/Texto";
import { ToastProvider, useToast } from "@/ui/componentes/Toast";
import { espaco } from "@/ui/tokens";

/**
 * As 9 chaves de `_LABELS` (`core/services/pluggy_health.py`) + 1
 * desconhecida, para provar o fallback neutro. `label` é texto obviamente
 * FALSO de propósito (CLAUDE.md §0.7): o catálogo prova tom e ícone por
 * `estado`, nunca o texto — `ConnectionStatus` promete não guardar cópia
 * própria do rótulo, e repetir aqui os 9 textos reais do backend
 * contradizia essa promessa (o servidor é a única fonte).
 */
const ESTADOS_CONEXAO = [
  { estado: "updated", label: "rótulo do servidor (updated)" },
  { estado: "partial", label: "rótulo do servidor (partial)" },
  { estado: "updating", label: "rótulo do servidor (updating)" },
  { estado: "error_recoverable", label: "rótulo do servidor (error_recoverable)" },
  { estado: "needs_user_action", label: "rótulo do servidor (needs_user_action)" },
  { estado: "item_missing", label: "rótulo do servidor (item_missing)" },
  { estado: "paused", label: "rótulo do servidor (paused)" },
  { estado: "removed", label: "rótulo do servidor (removed)" },
  { estado: "no_accounts", label: "rótulo do servidor (no_accounts)" },
  { estado: "estado_novo_do_backend", label: "rótulo do servidor (estado_novo_do_backend)" },
];

function BotaoToast() {
  const { mostrar } = useToast();
  return (
    <View style={{ gap: espaco.sm }}>
      <Button rotulo="Mostrar sucesso" variante="secondary" onPress={() => mostrar({ mensagem: "Transação salva!", tom: "sucesso" })} />
      <Button rotulo="Mostrar erro (substitui)" variante="secondary" onPress={() => mostrar({ mensagem: "Não foi possível salvar.", tom: "erro" })} />
    </View>
  );
}

/**
 * Catálogo de `EmptyState`, `ConnectionStatus` e `Toast` (PR C2) — fora de
 * `app/_ds/index.tsx` por tamanho (CLAUDE.md §0.5, mesmo motivo de
 * `composicao.tsx`).
 */
export function SecaoEstado() {
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Estado</Texto>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          EmptyState
        </Texto>
        <Card elevacao="raised">
          <EmptyState sticker="thinking" frase="Nenhum lançamento neste período." acao={{ rotulo: "Limpar filtro", onPress: () => {} }} />
        </Card>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          ConnectionStatus
        </Texto>
        <Card elevacao="raised">
          <View style={{ gap: espaco.lg }}>
            {ESTADOS_CONEXAO.map((item) => (
              <ConnectionStatus key={item.estado} estado={item.estado} label={item.label} />
            ))}
          </View>
        </Card>
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          Toast
        </Texto>
        {/* Altura fixa: a caixa flutuante do toast fica dentro dela, não sobre o resto do catálogo. */}
        <View style={{ height: 160 }}>
          <ToastProvider>
            <BotaoToast />
          </ToastProvider>
        </View>
      </View>
    </View>
  );
}
