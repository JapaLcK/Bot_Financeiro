import { ActivityIndicator, View } from "react-native";

import { useTema } from "@/ui/tema";
import { espaco, type Paleta } from "@/ui/tokens";

import { Button } from "./Button";
import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

interface Acao {
  rotulo: string;
  onPress: () => void;
}

interface Props {
  /** Vem do servidor (`core/services/pluggy_health.py`, `_LABELS`) — nunca fixo aqui. */
  estado: string;
  /** Texto pronto do servidor: este componente NÃO guarda cópia própria (CLAUDE.md §0.7 — uma fonte de verdade é o backend). */
  label: string;
  detalhe?: string;
  acao?: Acao;
}

interface ItemVisual {
  tom: keyof Paleta;
  icone?: NomeIcone;
  spinner?: boolean;
}

/**
 * Só mapeia estado→(tom, ícone) — o TEXTO é sempre `label`/`detalhe` (prop).
 * Chaves = as 9 de `_LABELS` (`core/services/pluggy_health.py`);
 * `tests/test_app_espelhos.py` compara os dois conjuntos por regex, então uma
 * chave nova lá sem entrada aqui quebra o CI, não silenciosamente em produção
 * (ela cai no visual `PADRAO` abaixo, nunca lança).
 */
const VISUAL: Record<string, ItemVisual> = {
  updated: { tom: "positive", icone: "CheckCircle" },
  partial: { tom: "warning", icone: "WarningCircle" },
  updating: { tom: "inkMuted", spinner: true },
  error_recoverable: { tom: "warning", icone: "WarningCircle" },
  needs_user_action: { tom: "warning", icone: "WarningCircle" },
  item_missing: { tom: "danger", icone: "LinkBreak" },
  paused: { tom: "inkMuted", icone: "PauseCircle" },
  removed: { tom: "inkMuted", icone: "Trash" },
  no_accounts: { tom: "inkMuted", icone: "Info" },
};

/** Estado que o backend ainda não tem no mapa: nunca lança, mostra o `label` dele mesmo assim. */
const PADRAO: ItemVisual = { tom: "inkMuted", icone: "Question" };

export function ConnectionStatus({ estado, label, detalhe, acao }: Props) {
  const { cores } = useTema();
  const visual = VISUAL[estado] ?? PADRAO;

  return (
    <View style={{ flexDirection: "row", alignItems: "flex-start", gap: espaco.md }}>
      {visual.spinner ? (
        // Escondido do leitor de tela: o `Texto` (`label`) ao lado já diz o
        // mesmo estado — sem isto o VoiceOver lia "Atualizando, Atualizando…"
        // (o rótulo fixo do spinner seguido do texto).
        <ActivityIndicator
          color={cores[visual.tom]}
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
        />
      ) : (
        <Icone nome={visual.icone ?? "Question"} tom={visual.tom} />
      )}
      <View style={{ flex: 1, gap: espaco.xs }}>
        <Texto variante="rotulo" tom={visual.tom}>
          {label}
        </Texto>
        {detalhe ? (
          <Texto variante="legenda" tom="inkMuted">
            {detalhe}
          </Texto>
        ) : null}
        {acao ? <Button rotulo={acao.rotulo} variante="ghost" tamanho="M" onPress={acao.onPress} /> : null}
      </View>
    </View>
  );
}
