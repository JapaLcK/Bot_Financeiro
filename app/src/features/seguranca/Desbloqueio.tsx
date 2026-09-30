import { useEffect, useState } from "react";
import { Switch, View } from "react-native";

import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

type Capacidade = Awaited<ReturnType<ReturnType<typeof useBloqueio>["capacidade"]>>;

const ERRO_COFRE = "Não conseguimos salvar sua escolha neste aparelho. Tente de novo.";

/**
 * Liga e desliga a trava deste aparelho. Desligar pede o Face ID antes
 * (`desligar()` no provider); o switch mostra o que está GRAVADO, então um
 * cancelamento ou uma falha do cofre o deixam onde estava.
 */
export function Desbloqueio() {
  const { cores } = useTema();
  const { estado, desligar, ligar, capacidade } = useBloqueio();
  // `undefined` = ainda perguntando ao aparelho.
  const [tipo, setTipo] = useState<Capacidade | undefined>(undefined);
  const [mudando, setMudando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    void capacidade().then(setTipo);
  }, []);

  if (tipo === undefined) return null;

  const rotulo = `Desbloqueio com ${tipo ?? "o código do aparelho"}`;
  const trocar = async (ligada: boolean) => {
    setErro(null);
    setMudando(true);
    const r = await (ligada ? ligar() : desligar());
    setMudando(false);
    if (r === "erro") setErro(ERRO_COFRE);
  };

  return (
    <View style={{ gap: espaco.sm }}>
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: espaco.md }}>
        <Texto variante="secao" style={{ flex: 1 }}>
          {rotulo}
        </Texto>
        <Switch
          accessibilityLabel={rotulo}
          value={tipo !== null && estado.preferencia === "ligada"}
          disabled={tipo === null || mudando}
          onValueChange={(v) => void trocar(v)}
          trackColor={{ true: cores.acao }}
        />
      </View>
      <Texto variante="corpo" tom="inkMuted">
        {tipo === null
          ? "Configure um código no aparelho para usar."
          : "Pede ao abrir o PigBank e quando você volta depois de 1 minuto fora."}
      </Texto>
      {erro ? (
        <Texto variante="corpo" tom="danger">
          {erro}
        </Texto>
      ) : null}
    </View>
  );
}
