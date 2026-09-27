import * as AppleAuthentication from "expo-apple-authentication";
import { router } from "expo-router";
import { useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, View } from "react-native";

import { continuarComApple } from "@/features/auth/apple";
import { CodigoMfa } from "@/features/auth/CodigoMfa";
import { CompletarCadastroSocial } from "@/features/auth/CompletarCadastroSocial";
import { MENSAGEM_ERRO_COFRE, apagaSenhaNaFase, enviar, tentarDeNovo, tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { continuarComGoogle } from "@/features/auth/google";
import { useSessao } from "@/features/auth/sessao";
import { Banner } from "@/ui/componentes/Banner";
import { ALTURA, Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { Input } from "@/ui/componentes/Input";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

/**
 * Formulário e fase de código do MFA na MESMA rota (`CodigoMfa`, montado
 * quando `estado.fase` é "mfa"/"verificando"). O desafio nunca vira parâmetro
 * de rota — é uma credencial de 5 minutos, e fica só no `useState` local. O
 * cadastro de quem entra pelo Google ou pela Apple sem conta também
 * (`CompletarCadastroSocial`).
 */
export default function Entrar() {
  const { cores, esquema } = useTema();
  const sessao = useSessao();
  const avisoInicial = sessao.estado.fase === "anonimo" ? sessao.estado.aviso : undefined;
  const [estado, setEstado] = useState<EstadoEntrar>({ fase: "formulario", aviso: avisoInicial });
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");

  const enviando = estado.fase === "enviando";
  const google = estado.fase === "google";
  const apple = estado.fase === "apple";
  // Uma ação por vez: com o login, o Google ou a Apple em voo, nada mais do formulário responde.
  const ocupado = enviando || google || apple;
  const cadastro = estado.fase === "cadastro-social" || estado.fase === "criando-social";
  const avisoFormulario = estado.fase === "formulario" ? estado.aviso : undefined;
  // Voltar a digitar é uma tentativa nova: o aviso da anterior sai da tela.
  const digitar = (definir: (v: string) => void) => (v: string) => {
    definir(v);
    if (avisoFormulario) setEstado({ fase: "formulario" });
  };
  // Por que a senha fica ou sai em cada fase: `apagaSenhaNaFase` (entrar.ts).
  const aplicar = (e: EstadoEntrar) => {
    if (apagaSenhaNaFase(e.fase)) setSenha("");
    setEstado(e);
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <Screen>
        <View style={{ gap: espaco.xl, paddingTop: espaco.xxl }}>
          <Texto variante="titulo">
            {cadastro ? "Criar conta" : "Entrar"}
          </Texto>

          {estado.fase === "erro-cofre" ? (
            <Banner
              tom="danger"
              mensagem={estado.mensagem ?? MENSAGEM_ERRO_COFRE}
              acao={{ rotulo: "Tentar de novo", onPress: () => aplicar(tentarDeNovo()) }}
            />
          ) : estado.fase === "mfa" || estado.fase === "verificando" ? (
            <CodigoMfa estado={estado} autenticar={sessao.autenticar} aplicar={aplicar} />
          ) : estado.fase === "cadastro-social" || estado.fase === "criando-social" ? (
            <CompletarCadastroSocial estado={estado} autenticar={sessao.autenticar} aplicar={aplicar} />
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
                    desativado={ocupado}
                  />
                  <Input
                    rotulo="Senha"
                    icone="Lock"
                    value={senha}
                    onChangeText={digitar(setSenha)}
                    secureTextEntry
                    autoComplete="current-password"
                    textContentType="password"
                    desativado={ocupado}
                    erro={avisoFormulario}
                  />
                  <View style={{ alignSelf: "flex-end" }}>
                    <Button
                      rotulo="Esqueci a senha"
                      variante="ghost"
                      desativado={ocupado}
                      onPress={() => router.push("/esqueci-senha")}
                    />
                  </View>
                  <Button
                    rotulo="Entrar"
                    tamanho="L"
                    carregando={enviando}
                    desativado={google || apple || !email.trim() || !senha}
                    onPress={() => {
                      // Fase "enviando" aplicada AQUI, antes de `tocar()`: sem
                      // isto o busy só aparecia quando a resposta já tivesse
                      // chegado — tarde demais para desativar campo/botões
                      // durante a espera de verdade (B1). A guarda de UMA
                      // requisição continua sendo o `pendentes` de `tocar()`.
                      aplicar({ fase: "enviando" });
                      void tocar(() => enviar(email, senha, sessao.autenticar), aplicar);
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
                  carregando={google}
                  desativado={enviando || apple}
                  onPress={() => {
                    // G antes de `tocar()`, pelo mesmo motivo do Entrar (B1).
                    aplicar({ fase: "google" });
                    void tocar(() => continuarComGoogle(sessao.autenticar), aplicar);
                  }}
                />
                {/* Só iOS: no Android não há Apple (nem botão, nem legenda). Botão do
                    sistema (decisão do dono): não desenha carregando, então em A
                    ele fica inerte e o indicador aparece ao lado; em E e G, apagado. */}
                {Platform.OS === "ios" ? (
                  <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
                    <View
                      testID="apple-involucro"
                      style={{ flex: 1, opacity: enviando || google ? 0.5 : 1 }}
                      pointerEvents={ocupado ? "none" : "auto"}
                    >
                      <AppleAuthentication.AppleAuthenticationButton
                        // O nativo não redesenha ao trocar o estilo: `key` pelo tema remonta.
                        key={esquema}
                        buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
                        buttonStyle={
                          esquema === "dark"
                            ? AppleAuthentication.AppleAuthenticationButtonStyle.WHITE
                            : AppleAuthentication.AppleAuthenticationButtonStyle.WHITE_OUTLINE
                        }
                        cornerRadius={raio.md}
                        style={{ height: ALTURA.M }}
                        onPress={() => {
                          // A antes de `tocar()`, pelo mesmo motivo do Entrar (B1).
                          aplicar({ fase: "apple" });
                          void tocar(() => continuarComApple(sessao.autenticar), aplicar);
                        }}
                      />
                    </View>
                    {apple ? <ActivityIndicator color={cores.ink} accessibilityLabel="Entrando com a Apple" /> : null}
                  </View>
                ) : null}
              </View>

              <View style={{ alignItems: "center" }}>
                <Texto variante="legenda" tom="inkMuted">
                  Não tem conta?
                </Texto>
                <Button
                  rotulo="Criar conta"
                  variante="ghost"
                  desativado={ocupado}
                  onPress={() => router.push("/criar-conta")}
                />
              </View>
            </>
          )}
        </View>
      </Screen>
    </KeyboardAvoidingView>
  );
}
