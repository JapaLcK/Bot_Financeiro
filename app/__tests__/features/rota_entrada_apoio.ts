import { router, type Href } from "expo-router";
import { act, renderRouter } from "expo-router/testing-library";

/**
 * Abre uma rota de `(auth)` como o app abre: a partir de "/" e navegando por dentro.
 * O link FRIO para elas (`initialUrl: "/entrar"`) é descartado por `+native-intent.ts`
 * (#604), então não serve mais de atalho para os testes das telas.
 */
export function abrirRotaDeEntrada(rota: string) {
  const r = renderRouter("./app");
  act(() => {
    router.push(rota as Href);
  });
  return r;
}
