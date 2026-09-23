import { useEffect } from "react";
import { AccessibilityInfo, View } from "react-native";

import { useTema } from "@/ui/tema";
import { espaco, raio, type Paleta } from "@/ui/tokens";

import { Button } from "./Button";
import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

type Tom = "info" | "warning" | "danger";

interface Acao {
  rotulo: string;
  onPress: () => void;
}

interface Props {
  tom?: Tom;
  titulo?: string;
  mensagem: string;
  acao?: Acao;
}

/**
 * `info` usa `ink` (neutro), nunca `brand`: rosa é só onde há decisão
 * (identidade pigbank-frontend) — um banner informativo não é uma decisão.
 * Os três tons já estão em `PARES` como TEXTO×FUNDOS (≥4,5 sobre `surface`),
 * então nenhum par novo entra no teste de contraste.
 */
const VISUAL: Record<Tom, { tom: keyof Paleta; icone: NomeIcone }> = {
  info: { tom: "ink", icone: "Info" },
  warning: { tom: "warning", icone: "WarningCircle" },
  danger: { tom: "danger", icone: "WarningOctagon" },
};

export function Banner({ tom = "info", titulo, mensagem, acao }: Props) {
  const { cores } = useTema();
  const visual = VISUAL[tom];

  // `accessibilityRole="alert"` (abaixo) não garante o anúncio sozinho em
  // toda plataforma/leitor de tela; o anúncio explícito é o que o Toast já
  // faz (`ui/componentes/Toast.tsx`) para o mesmo problema. Só `danger`: é o
  // tom que carrega a role de alerta, e um banner `info`/`warning` que já
  // nasce na tela (não é um evento novo) não deveria interromper a leitura.
  useEffect(() => {
    if (tom === "danger") AccessibilityInfo.announceForAccessibility(mensagem);
  }, [tom, mensagem]);

  return (
    <View
      accessibilityRole={tom === "danger" ? "alert" : undefined}
      style={{
        flexDirection: "row",
        gap: espaco.md,
        padding: espaco.lg,
        borderRadius: raio.md,
        backgroundColor: cores.surface,
      }}
    >
      <Icone nome={visual.icone} tom={visual.tom} />
      <View style={{ flex: 1, gap: espaco.xs }}>
        {titulo ? (
          <Texto variante="rotulo" tom="ink">
            {titulo}
          </Texto>
        ) : null}
        <Texto variante="corpo" tom="inkMuted">
          {mensagem}
        </Texto>
        {acao ? <Button rotulo={acao.rotulo} variante="ghost" tamanho="M" onPress={acao.onPress} /> : null}
      </View>
    </View>
  );
}
