import { useEffect, useRef } from "react";
import { AccessibilityInfo, View, type Text } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { DESLIGAR_AGORA, TITULO_AVISO } from "./notificacoes";

/**
 * Peças dos Avisos do Piggy por e-mail (PL-06 B6) na tela de Notificações. Todo texto explicativo vem do servidor e é
 * mostrado como texto simples; aqui só ficam os rótulos curtos.
 */

/** Texto longo SOB a linha, fora do `ListRow`: a linha desativada fica com `opacity` 0,45 inteira e o texto perderia contraste. */
export function NotaDaLinha({ textos }: { textos: (string | null)[] }) {
  return textos.filter(Boolean).map((t) => (
    <Texto key={t} variante="legenda" tom="inkMuted" style={{ paddingBottom: espaco.sm }}>
      {t}
    </Texto>
  ));
}

/** Conta cortada dentro da margem, com adesão viva (D2): o texto da margem e o único caminho, desligar. Sem interruptor. */
export function BlocoSemPlano({ texto, ocupado, aoDesligar }: { texto: string; ocupado: boolean; aoDesligar: () => void }) {
  return (
    <View style={{ gap: espaco.sm }}>
      <Texto variante="rotulo" accessibilityRole="header">{TITULO_AVISO}</Texto>
      <Texto tom="inkMuted">{texto}</Texto>
      <Button rotulo={DESLIGAR_AGORA} variante="secondary" carregando={ocupado} desativado={ocupado} onPress={aoDesligar} />
    </View>
  );
}

/** Um objeto por desligamento; `focar` é decidido na hora (o controle que levou até ele sumiu), nunca pela fase da tela. */
export type EventoDesligou = { texto: string; focar: boolean };

/**
 * "Desligados": anunciado (o `Banner` `info` não anuncia) e focado uma vez por evento. O efeito depende do OBJETO: a releitura
 * não o troca, então nenhuma leitura anuncia nem foca de novo; só a próxima escrita (ou a troca de conta) cria outro.
 */
export function Desligou({ evento }: { evento: EventoDesligou }) {
  const ref = useRef<Text>(null);
  useEffect(() => {
    AccessibilityInfo.announceForAccessibility(evento.texto);
    if (evento.focar && ref.current) AccessibilityInfo.sendAccessibilityEvent(ref.current, "focus");
  }, [evento]);
  return <Texto ref={ref}>{evento.texto}</Texto>;
}
