import { View } from "react-native";

/**
 * Dublê de TODO ícone Phosphor no Jest (ver `moduleNameMapper` em
 * `jest.config.js`). Um `View` vazio: o traço do SVG não é o que o componente
 * `Icone` (PR C1) precisa provar — tamanho e cor são propriedades do
 * componente, não do desenho do ícone.
 */
export default function IconeStub() {
  return <View />;
}
