import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { conferirVolta, itemDoLink, type EstadoVolta } from "@/features/openFinance/volta";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/**
 * `<scheme>://open-finance-volta?itemId=…`: a Pluggy devolve o usuário aqui
 * depois do OAuth do banco. Dentro de `(app)`, então sem sessão nem monta
 * (`Stack.Protected`). A lógica é `features/openFinance/volta.ts`; aqui só a
 * trava, o cancelamento e o desenho. Os textos são PROVISÓRIOS até as telas 6–7.
 *
 * ponytail: abertura fria com a trava ligada mostra a `TelaDeBloqueio` no lugar
 * da pilha, e depois de liberar o roteador pode não reabrir esta rota (perde o
 * `itemId`); aí só o webhook adota o item. Teto conhecido, fecha com as telas 5–7.
 */
export default function OpenFinanceVolta() {
  const { cores } = useTema();
  const { expirou } = useSessao();
  const travado = useBloqueio().estado.fase === "travado";
  // Só o `itemId` do link é lido; `uid`/`user_id` nele são ignorados (o uid vem de `perfil()`).
  const item = itemDoLink(useLocalSearchParams().itemId);
  const [estado, setEstado] = useState<EstadoVolta>({ fase: "esperando-trava" });
  const [rodada, setRodada] = useState(0);

  // Nada de pedido com a trava na frente; se ela subir no meio, cancela, e ao
  // liberar recomeça com janela nova (o GET vem primeiro: não repete POST à toa).
  // `expirou` de fora das dependências, como o Início: muda a cada troca de sessão.
  useEffect(() => {
    if (travado) return setEstado({ fase: "esperando-trava" });
    let cancelado = false;
    void conferirVolta(item, {
      agora: Date.now,
      esperar: (ms) => new Promise((r) => setTimeout(r, ms)),
      cancelado: () => cancelado,
      aoMudar: setEstado,
      expirou,
    });
    return () => {
      cancelado = true;
    };
  }, [item, travado, rodada]);

  const continuar = <Button rotulo="Continuar" variante={estado.fase === "ainda-conferindo" ? "secondary" : "primary"} onPress={() => router.back()} />;

  return (
    <Screen rolar={false}>
      <View style={{ flex: 1, justifyContent: "center", gap: espaco.lg }}>
        <Texto variante="titulo">Conectando seu banco</Texto>

        {(estado.fase === "esperando-trava" || estado.fase === "conferindo") && (
          <View style={{ gap: espaco.md }}>
            <ActivityIndicator color={cores.brand} accessibilityLabel="Conferindo" />
            <Texto variante="corpo" tom="inkMuted">
              {estado.fase === "conferindo" && estado.instavel
                ? "A conexão está instável. Seguimos tentando."
                : "Estamos conferindo com o banco."}
            </Texto>
          </View>
        )}

        {estado.fase === "conectado" && (
          <>
            <ConnectionStatus estado={estado.ui.state} label={estado.ui.label} detalhe={estado.ui.detail ?? undefined} />
            {continuar}
          </>
        )}

        {estado.fase === "ainda-conferindo" && (
          <>
            <Texto variante="corpo" tom="inkMuted">
              O banco ainda não confirmou. Pode levar alguns minutos: ele aparece sozinho quando terminar.
            </Texto>
            <Button rotulo="Conferir de novo" onPress={() => setRodada((n) => n + 1)} />
            {continuar}
          </>
        )}

        {estado.fase === "sem-item" && (
          <>
            <Texto variante="corpo" tom="inkMuted">
              Se você conectou um banco, ele aparece em instantes.
            </Texto>
            {continuar}
          </>
        )}

        {estado.fase === "erro" && (
          <>
            <Banner tom="danger" mensagem={estado.texto} />
            {continuar}
          </>
        )}
      </View>
    </Screen>
  );
}
