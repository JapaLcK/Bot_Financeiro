import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { BackHandler, Image, KeyboardAvoidingView, Platform, View } from "react-native";

import { Apresentacao } from "@/features/auth/Apresentacao";
import { BotoesSociais, Ou } from "@/features/auth/BotoesSociais";
import type { EstadoEntrar } from "@/features/auth/entrar";
import { FasesDaEntrada } from "@/features/auth/FasesDaEntrada";
import { useSessao } from "@/features/auth/sessao";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

// `require()` pelo mesmo motivo de `src/ui/stickers.ts` (sem declaração de
// módulo `*.png`). Cópia de `frontend/brand/email-logo.png`, 181×192 = 64 pt
// @3x — `tests/test_app_espelhos.py` compara os bytes.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const SIMBOLO = require("../../assets/brand/simbolo.png");

/**
 * A base da pilha sem sessão: Google, Apple, Criar conta e Já tenho conta.
 * O Google e a Apple rodam AQUI (MFA e cadastro social também, pela
 * `FasesDaEntrada`), com estado local — o Entrar tem o dele.
 */
export default function BoasVindas() {
  const sessao = useSessao();
  const [iniciou, setIniciou] = useState(false);
  const [estado, setEstado] = useState<EstadoEntrar>({ fase: "formulario" });
  const ocupado = estado.fase === "google" || estado.fase === "apple";
  const repouso = estado.fase === "formulario" || ocupado;
  const cadastro = estado.fase === "cadastro-social" || estado.fase === "criando-social";
  const aviso = estado.fase === "formulario" ? estado.aviso : undefined;

  // Sessão expirada (só o `expirou()` põe aviso; boot e Sair não): o Entrar
  // com o aviso por cima, esta embaixo para a seta voltar (P1 do dono).
  // `[]`: só na montagem — voltar do Entrar não pode empurrá-lo de novo.
  useEffect(() => {
    if (sessao.estado.fase === "anonimo" && sessao.estado.aviso) router.push("/entrar");
  }, []);

  // Começar muda uma etapa local, sem acrescentar uma rota à pilha. No
  // Android, Voltar retorna à apresentação como o botão da tela; durante
  // Google/Apple, fica bloqueado. Perder o foco remove o ouvinte para que
  // Entrar e Criar conta continuem usando o histórico de navegação.
  useFocusEffect(
    useCallback(() => {
      if (!iniciou || !repouso) return;
      const inscricao = BackHandler.addEventListener("hardwareBackPress", () => {
        if (!ocupado) setIniciou(false);
        return true;
      });
      return () => inscricao.remove();
    }, [iniciou, repouso, ocupado]),
  );

  // O aviso de um Google/Apple que falhou sai no próximo toque.
  const ir = (rota: "/criar-conta" | "/entrar") => {
    if (aviso) setEstado({ fase: "formulario" });
    router.push(rota);
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <Screen>
        {!iniciou && repouso ? (
          <Apresentacao comecar={() => setIniciou(true)} entrar={() => ir("/entrar")} />
        ) : <View
          style={
            repouso
              ? { flex: 1, gap: espaco.xl, paddingVertical: espaco.lg }
              : { gap: espaco.xl, paddingTop: espaco.xxl }
          }
        >
          {repouso ? <Button rotulo="Voltar" variante="ghost" desativado={ocupado} onPress={() => setIniciou(false)} /> : <Texto variante="titulo">{cadastro ? "Criar conta" : "Entrar"}</Texto>}
          <FasesDaEntrada estado={estado} aplicar={setEstado} autenticar={sessao.autenticar}>
            <View style={{ gap: espaco.md }}>
              <Image source={SIMBOLO} accessible={false} resizeMode="contain" style={{ width: 34, height: 37 }} />
              <Texto variante="titulo" accessibilityRole="header">
                Crie sua conta
              </Texto>
              <Texto variante="corpo" tom="inkMuted">
                Sua grana. Tudo mais claro.
              </Texto>
            </View>

            <View style={{ gap: espaco.lg }}>
              {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
              <BotoesSociais estado={estado} aplicar={setEstado} autenticar={sessao.autenticar} />
              <Ou />
              <View style={{ gap: espaco.sm }}>
                <Button rotulo="Criar conta" tamanho="L" desativado={ocupado} onPress={() => ir("/criar-conta")} />
                <Button rotulo="Já tenho conta" variante="ghost" desativado={ocupado} onPress={() => ir("/entrar")} />
              </View>
            </View>
          </FasesDaEntrada>
        </View>}
      </Screen>
    </KeyboardAvoidingView>
  );
}
