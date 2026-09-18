import { router } from "expo-router";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, View } from "react-native";

import { CodigoMfa } from "@/features/auth/CodigoMfa";
import { enviar, tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";
import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { Input } from "@/ui/componentes/Input";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/**
 * Formulário e fase de código do MFA na MESMA rota (`CodigoMfa`, montado
 * quando `estado.fase` é "mfa"/"verificando"). O desafio nunca vira parâmetro
 * de rota — é uma credencial de 5 minutos, e fica só no `useState` local.
 */
export default function Entrar() {
  const { cores } = useTema();
  const sessao = useSessao();
  const avisoInicial = sessao.estado.fase === "anonimo" ? sessao.estado.aviso : undefined;
  const [estado, setEstado] = useState<EstadoEntrar>({ fase: "formulario", aviso: avisoInicial });
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");

  const enviando = estado.fase === "enviando";
  const avisoFormulario = estado.fase === "formulario" ? estado.aviso : undefined;

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <Screen>
        <View style={{ gap: espaco.xl, paddingTop: espaco.xxl }}>
          <Texto variante="titulo">Entrar</Texto>

          {estado.fase === "mfa" || estado.fase === "verificando" ? (
            <CodigoMfa estado={estado} autenticar={sessao.autenticar} aplicar={setEstado} />
          ) : (
            <>
              <Card>
                <View style={{ gap: espaco.lg }}>
                  <Input
                    rotulo="E-mail"
                    icone="Envelope"
                    value={email}
                    onChangeText={setEmail}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="email-address"
                    textContentType="username"
                    desativado={enviando}
                  />
                  <Input
                    rotulo="Senha"
                    icone="Lock"
                    value={senha}
                    onChangeText={setSenha}
                    secureTextEntry
                    textContentType="password"
                    desativado={enviando}
                    erro={avisoFormulario}
                  />
                  <View style={{ alignSelf: "flex-end" }}>
                    <Button
                      rotulo="Esqueci a senha"
                      variante="ghost"
                      desativado={enviando}
                      onPress={() => router.push("/esqueci-senha")}
                    />
                  </View>
                  <Button
                    rotulo="Entrar"
                    tamanho="L"
                    carregando={enviando}
                    desativado={!email.trim() || !senha}
                    onPress={() => {
                      // A senha sai da tela ANTES da requisição: voltar ao
                      // formulário nunca a mostra preenchida.
                      const s = senha;
                      setSenha("");
                      void tocar(() => enviar(email, s, sessao.autenticar), setEstado);
                    }}
                  />
                </View>
              </Card>

              <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
                <View style={{ flex: 1, height: 1, backgroundColor: cores.border }} />
                <Texto variante="legenda" tom="inkMuted">
                  ou
                </Texto>
                <View style={{ flex: 1, height: 1, backgroundColor: cores.border }} />
              </View>

              <View style={{ gap: espaco.sm }}>
                <Button rotulo="Continuar com Google" variante="secondary" icone="GoogleLogo" desativado onPress={() => {}} />
                <Button rotulo="Continuar com Apple" variante="secondary" icone="AppleLogo" desativado onPress={() => {}} />
                <Texto variante="legenda" tom="inkMuted" style={{ textAlign: "center" }}>
                  Em breve
                </Texto>
              </View>
            </>
          )}
        </View>
      </Screen>
    </KeyboardAvoidingView>
  );
}
