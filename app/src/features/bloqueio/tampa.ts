import Constants, { ExecutionEnvironment } from "expo-constants";
import { requireNativeModule } from "expo";
import { Platform } from "react-native";

/**
 * A tampa nativa de privacidade (`modules/tampa/ios/TampaModule.swift`). Só
 * existe no iOS do nosso binário: no Android e no Expo Go vira no-op. No nosso
 * binário iOS sem o módulo, `requireNativeModule` LANÇA na abertura — de
 * propósito: um build que perdeu a tampa não pode sair mostrando o conteúdo.
 */
type Tampa = { descobrir(): Promise<void>; pular(v: boolean): Promise<void> };

const nativo: Tampa | null =
  Platform.OS === "ios" && Constants.executionEnvironment !== ExecutionEnvironment.StoreClient
    ? requireNativeModule<Tampa>("PigBankTampa")
    : null;

/** Tira a tampa (se o app ainda estiver ativo quando o nativo for olhar). Não espera. */
export function descobrir(): void {
  void nativo?.descobrir();
}

/** Liga/desliga a tampa para os NOSSOS prompts de Face ID. Espere antes de pedir. */
export function pular(v: boolean): Promise<void> {
  return nativo ? nativo.pular(v) : Promise.resolve();
}
