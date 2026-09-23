import { router } from "expo-router";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, View } from "react-native";

import { CodigoMfa } from "@/features/auth/CodigoMfa";
import { MENSAGEM_ERRO_COFRE, enviar, tentarDeNovo, tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";
import { Banner } from "@/ui/componentes/Banner";
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
  // Voltar a digitar é uma tentativa nova: o aviso da anterior sai da tela.
  const digitar = (definir: (v: string) => void) => (v: string) => {
    definir(v);
    if (avisoFormulario) setEstado({ fase: "formulario" });
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <Screen>
        <View style={{ gap: espaco.xl, paddingTop: espaco.xxl }}>
          <Texto variante="titulo">Entrar</Texto>

          {estado.fase === "erro-cofre" ? (
            <Banner
              tom="danger"
              mensagem={MENSAGEM_ERRO_COFRE}
              acao={{ rotulo: "Tentar de novo", onPress: () => setEstado(tentarDeNovo()) }}
            />
          ) : estado.fase === "mfa" || estado.fase === "verificando" ? (
            <CodigoMfa estado={estado} autenticar={sessao.autenticar} aplicar={setEstado} />
          ) : (
            <>
              <Card>
                <View style={{ gap: espaco.lg }}>
                  <Input
                    rotulo="E-mail"
                    icone="Envelope"
                    value={email}
                    onChangeText={digitar(setEmail)}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="email-address"
                    autoComplete="email"
                    textContentType="username"
                    desativado={enviando}
                  />
                  <Input
                    rotulo="Senha"
                    icone="Lock"
                    value={senha}
                    onChangeText={digitar(setSenha)}
                    secureTextEntry
                    autoComplete="current-password"
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
                      // Fase "enviando" aplicada AQUI, antes de `tocar()`: sem
                      // isto o busy só aparecia quando a resposta já tivesse
                      // chegado — tarde demais para desativar campo/botões
                      // durante a espera de verdade (B1). A guarda de UMA
                      // requisição continua sendo o `pendentes` de `tocar()`.
                      setEstado({ fase: "enviando" });
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
                <Button
                  rotulo="Continuar com Google"
                  variante="secondary"
                  icone="GoogleLogo"
                  desativado
                  accessibilityHint="Em breve"
                  onPress={() => {}}
                />
                <Button
                  rotulo="Continuar com Apple"
                  variante="secondary"
                  icone="AppleLogo"
                  desativado
                  accessibilityHint="Em breve"
                  onPress={() => {}}
                />
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
