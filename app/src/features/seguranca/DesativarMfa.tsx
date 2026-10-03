import { Alert } from "react-native";

import { desativarMfa } from "@/services/mfa";

import { SenhaECodigo } from "./SenhaECodigo";

/** Resolve `true` só no "Desativar"; cancelar ou dispensar o alerta é `false`. */
function confirmar(): Promise<boolean> {
  return new Promise((resolver) =>
    Alert.alert(
      "Desativar a verificação em 2 etapas?",
      "Sua conta volta a pedir só a senha para entrar.",
      [
        { text: "Cancelar", style: "cancel", onPress: () => resolver(false) },
        { text: "Desativar", style: "destructive", onPress: () => resolver(true) },
      ],
      { cancelable: true, onDismiss: () => resolver(false) },
    ),
  );
}

/**
 * Senha + código do app OU de backup (o servidor tenta os dois, e normaliza
 * hífen, espaço e caixa). Não encerra a sessão atual (`disable_mfa` só apaga
 * o MFA): no sucesso, fecha e a Segurança recarrega no foco.
 */
export function DesativarMfa({ aoConcluir }: { aoConcluir: () => void }) {
  return (
    <SenhaECodigo
      titulo="Desativar a verificação em 2 etapas"
      texto="Confirme sua senha e um código do app autenticador ou de backup."
      soTotp={false}
      rotuloBotao="Desativar"
      varianteBotao="danger"
      confirmar={confirmar}
      enviar={async (senha, codigo) => {
        await desativarMfa(senha, codigo);
        aoConcluir();
      }}
    />
  );
}
