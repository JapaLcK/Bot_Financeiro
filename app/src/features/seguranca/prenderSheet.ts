import { router } from "expo-router";
import { usePreventRemove } from "expo-router/react-navigation";
import { useEffect, useState } from "react";

/**
 * Prende a sheet desde o ENVIO da requisição que gera códigos de backup
 * (enable, regenerate) até "Já guardei meus códigos": eles aparecem UMA vez só
 * (o servidor guarda só o hash), e fechar com a requisição em voo ou com os
 * códigos na tela trancaria a pessoa fora da recuperação — no regenerate, os
 * antigos já deixaram de valer.
 *
 * `prender()` antes do envio; `desprender()` em QUALQUER falha (a pessoa fica
 * na sheet vendo o erro e pode sair); `soltar()` no "Já guardei", que solta e
 * sai. A sessão encerrada não depende do `desprender`: o `Stack.Protected` tira
 * a rota sem passar pelo `usePreventRemove` (`seguranca_rotas.test.tsx`, com a
 * trava nunca solta, ainda vai para /entrar).
 *
 * `preso` vai também no `gestureEnabled` da rota (quem chama declara o
 * `<Stack.Screen>`): o `usePreventRemove` segura o "voltar" e o botão do
 * Android; o gesto de arrastar a sheet no iOS é do `gestureEnabled`.
 *
 * O `router.back()` sai num efeito, DEPOIS do render que já soltou a trava —
 * chamado junto do `setState`, ele ainda veria a trava ligada.
 */
export function usePrenderSheet() {
  const [emRisco, setEmRisco] = useState(false);
  const [saindo, setSaindo] = useState(false);
  const preso = emRisco && !saindo;

  usePreventRemove(preso, () => undefined);

  useEffect(() => {
    if (saindo) router.back();
  }, [saindo]);

  return {
    preso,
    prender: () => setEmRisco(true),
    desprender: () => setEmRisco(false),
    soltar: () => setSaindo(true),
  };
}
