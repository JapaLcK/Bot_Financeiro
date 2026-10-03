import { Platform, Share, View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

const MONO = Platform.select({ ios: "Menlo", default: "monospace" });

interface Props {
  codigos: string[];
  onConcluir: () => void;
}

/**
 * Os códigos de backup, que o servidor mostra UMA vez (`db/mfa.py` só guarda
 * o hash). "Salvar códigos" abre a folha de compartilhar do sistema — Notas,
 * gerenciador de senhas, onde a pessoa quiser — sem dependência nova. Quem
 * chama é quem prende a sheet até `onConcluir` (`prenderSheet.ts`).
 */
export function CodigosBackup({ codigos, onConcluir }: Props) {
  const { cores } = useTema();
  const lista = codigos.join("\n");

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Guarde seus códigos de backup</Texto>
      <Texto variante="corpo" tom="inkMuted">
        Cada um serve uma vez para entrar se você ficar sem o app autenticador. Você não verá esses códigos de novo.
      </Texto>
      <View style={{ backgroundColor: cores.surface, borderRadius: raio.md, padding: espaco.lg }}>
        <Texto selectable style={{ fontFamily: MONO, letterSpacing: 1 }}>
          {lista}
        </Texto>
      </View>
      <Button rotulo="Salvar códigos" variante="secondary" onPress={() => void Share.share({ message: lista }).catch(() => undefined)} />
      <Button rotulo="Já guardei meus códigos" onPress={onConcluir} />
    </View>
  );
}
