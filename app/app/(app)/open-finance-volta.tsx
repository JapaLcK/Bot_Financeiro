import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { AccessibilityInfo, View } from "react-native";

import { useForeground } from "@/features/openFinance/useForeground";
import { useSessao } from "@/features/auth/sessao";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { conferirVolta, itemDoLink, type EstadoVolta } from "@/features/openFinance/volta";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { ConnectionStatus } from "@/ui/componentes/ConnectionStatus";
import { ProgressBarIndeterminada } from "@/ui/componentes/ProgressBar";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

/** Em `conferindo` há tanto tempo, aparece "Sair" com o aviso de demora. */
const DEMORA_MS = 30_000;
const ORGANIZANDO = "Organizando seus dados";

/** Relógio de parede a cada 1 s enquanto `ativo`: volta do segundo plano já certa. */
function useAgora(ativo: boolean): number {
  const [agora, setAgora] = useState(Date.now);
  useEffect(() => {
    if (!ativo) return;
    setAgora(Date.now());
    const id = setInterval(() => setAgora(Date.now()), 1000);
    return () => clearInterval(id);
  }, [ativo]);
  return agora;
}

const plural = (n: number, palavra: string) => `${n} ${palavra}${n === 1 ? "" : "s"}`;

function Contador({ ms }: { ms: number }) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const [min, seg] = [Math.floor(s / 60), s % 60];
  return (
    <Texto
      variante="legenda"
      tom="inkMuted"
      numerico
      accessibilityLabel={min ? `há ${plural(min, "minuto")} e ${plural(seg, "segundo")}` : `há ${plural(seg, "segundo")}`}
    >
      {min ? `há ${min} min ${seg} s` : `há ${seg} s`}
    </Texto>
  );
}

/**
 * `<scheme>://open-finance-volta?itemId=…`: a Pluggy devolve o usuário aqui
 * depois do OAuth do banco. Dentro de `(app)`, então sem sessão nem monta
 * (`Stack.Protected`). A lógica é `features/openFinance/volta.ts`; aqui só a
 * trava, o cancelamento e o desenho. Os textos são PROVISÓRIOS até as telas 6–7.
 * Enquanto espera (trava, conferindo, `updating`) a tela é "Organizando seus
 * dados": barra sem porcentagem (o servidor não diz quanto falta) e contador.
 * "Sair" só quando sair é seguro (o item já é conexão, ou ainda conferindo
 * `DEMORA_MS` depois da abertura ou do "Conferir de novo"); "Continuar" só nos
 * estados finais. O gesto de voltar
 * nunca é bloqueado.
 *
 */
export default function OpenFinanceVolta() {
  const { expirou } = useSessao();
  const ativo = useForeground();
  const travado = useBloqueio().estado.fase === "travado";
  // Só o `itemId` do link é lido; `uid`/`user_id` nele são ignorados (o uid vem de `perfil()`).
  const recebido = useLocalSearchParams().itemId;
  const origem = useLocalSearchParams().tentativaId;
  const item = itemDoLink(recebido);
  const link = item ?? (recebido === undefined ? undefined : "");
  const [estado, setEstado] = useState<EstadoVolta>({ fase: "esperando-trava" });
  const [rodada, setRodada] = useState(0);

  // Nada de pedido com a trava na frente; se ela subir no meio, cancela, e ao
  // liberar recomeça com janela nova (o GET vem primeiro: não repete POST à toa).
  // `expirou` de fora das dependências, como o Início: muda a cada troca de sessão.
  useEffect(() => {
    if (travado || !ativo) return setEstado({ fase: "esperando-trava" });
    let cancelado = false;
    const controlador = new AbortController();
    void conferirVolta(link, {
      agora: Date.now,
      controlador,
      esperar: (ms) => new Promise((r) => setTimeout(r, ms)),
      cancelado: () => cancelado,
      aoMudar: setEstado,
      expirou,
    }, origem);
    return () => {
      cancelado = true;
      controlador.abort();
    };
  }, [link, origem, travado, ativo, rodada]);

  const atualizando = estado.fase === "conectado" && estado.ui.state === "updating";
  const conferindo = estado.fase === "conferindo";
  const esperando = atualizando || conferindo || estado.fase === "esperando-trava";
  // Não zera em "Conferir de novo": mede a espera total; `rodadaDesde` zera os 30 s do Sair.
  const inicio = useMemo(() => Date.now(), [item]);
  // Os 30 s contam da abertura (ou do "Conferir de novo"), não da última entrada
  // em `conferindo`: a trava subindo e liberando no meio não zera a demora.
  const rodadaDesde = useMemo(() => Date.now(), [item, rodada]);
  const agora = useAgora(esperando);
  const demorando = conferindo && agora - rodadaDesde >= DEMORA_MS;

  // O fim da espera é um evento: o VoiceOver anuncia o estado final uma vez.
  useEffect(() => {
    if (estado.fase === "conectado" && estado.ui.state !== "updating") AccessibilityInfo.announceForAccessibility(estado.ui.label);
  }, [estado]);

  const sair = <Button rotulo="Sair" onPress={() => router.replace("/")} />;
  const continuar = <Button rotulo="Continuar" variante={estado.fase === "ainda-conferindo" ? "secondary" : "primary"} onPress={() => router.replace("/")} />;

  return (
    <Screen rolar={false}>
      <View style={{ flex: 1, justifyContent: "center", gap: espaco.lg }}>
        <Texto variante="titulo">{esperando || estado.fase === "organizando" ? ORGANIZANDO : "Conectando seu banco"}</Texto>

        {esperando && (
          <View style={{ gap: espaco.sm }}>
            <ProgressBarIndeterminada rotulo={ORGANIZANDO} />
            <Contador ms={agora - inicio} />
          </View>
        )}

        {(estado.fase === "esperando-trava" || conferindo) && (
          <Texto variante="corpo" tom="inkMuted">
            {conferindo && estado.instavel ? "A conexão está instável. Seguimos tentando." : "Estamos conferindo com o banco."}
          </Texto>
        )}

        {demorando && (
          <>
            <Texto variante="corpo" tom="inkMuted">
              Está demorando mais que o normal. Você pode sair desta tela e conferir depois.
            </Texto>
            {sair}
          </>
        )}

        {atualizando && (
          <>
            <Texto variante="corpo" tom="inkMuted">
              Isso pode levar alguns minutos.
            </Texto>
            <Texto variante="corpo" tom="inkMuted">
              Você pode sair desta tela: seus dados aparecem sozinhos quando terminar.
            </Texto>
            {sair}
          </>
        )}

        {estado.fase === "conectado" && !atualizando && (
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

        {estado.fase === "organizando" && (
          <>
            <Texto variante="corpo" tom="inkMuted">
              Seu banco foi conectado. Estamos organizando seus dados; eles aparecem sozinhos quando terminar.
            </Texto>
            <Button rotulo="Conferir de novo" variante="secondary" onPress={() => setRodada((n) => n + 1)} />
            {continuar}
          </>
        )}

        {estado.fase === "escolher-conexao" && (
          <>
            <Texto tom="inkMuted">Não conseguimos identificar o retorno desta tentativa. Confira os bancos conectados para acompanhar o estado de cada um.</Texto>
            <Button rotulo="Ver bancos conectados" onPress={() => router.replace("/conexoes")} />
            <Button rotulo="Conferir de novo" variante="secondary" onPress={() => setRodada((n) => n + 1)} />
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
