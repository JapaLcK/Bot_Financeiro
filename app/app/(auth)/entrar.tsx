import { router, Stack } from "expo-router";
import { usePreventRemove } from "expo-router/react-navigation";
import { useState } from "react";
import { KeyboardAvoidingView, Platform, View } from "react-native";

import { BotoesSociais, Ou } from "@/features/auth/BotoesSociais";
import { apagaSenhaNaFase, enviar, tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { FasesDaEntrada } from "@/features/auth/FasesDaEntrada";
import { useSessao } from "@/features/auth/sessao";
import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { Input } from "@/ui/componentes/Input";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

/**
 * Formulário e fase de código do MFA na MESMA rota (`CodigoMfa`, montado
 * quando `estado.fase` é "mfa"/"verificando"). O desafio nunca vira parâmetro
 * de rota — é uma credencial de 5 minutos, e fica só no `useState` local. O
 * cadastro de quem entra pelo Google ou pela Apple sem conta também
 * (`CompletarCadastroSocial`).
 */
export default function Entrar() {
  const sessao = useSessao();
  const avisoInicial = sessao.estado.fase === "anonimo" ? sessao.estado.aviso : undefined;
  const [estado, setEstado] = useState<EstadoEntrar>({ fase: "formulario", aviso: avisoInicial });
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  // Acesso de visualização (condomínio): usuário e senha que o dono definiu, sem Google, sem Apple e sem MFA.
  const [leitura, setLeitura] = useState(false);
  const alternarLeitura = () => { setLeitura((v) => !v); setEmail(""); setSenha(""); setEstado({ fase: "formulario" }); };

  const enviando = estado.fase === "enviando";
  const google = estado.fase === "google";
  const apple = estado.fase === "apple";
  // Uma ação por vez: com o login, o Google ou a Apple em voo, nada mais do formulário responde.
  const ocupado = enviando || google || apple;
  const cadastro = estado.fase === "cadastro-social" || estado.fase === "criando-social";
  // Com uma ação em voo, sair da rota a deixaria terminar sem ninguém para
  // mostrar o resultado — e a fila de `entrar.ts` é do MÓDULO: a Boas-vindas,
  // embaixo na pilha, ficaria presa no Google/Apple dela. Mesmo par
  // `usePreventRemove` + `gestureEnabled` do `criar-conta.tsx`.
  const emVoo = ocupado || estado.fase === "verificando" || estado.fase === "criando-social";
  usePreventRemove(emVoo, () => undefined);
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
      <Screen sobCabecalho ajustarTeclado={false}>
        <Stack.Screen options={{ gestureEnabled: !emVoo }} />
        <View style={{ gap: espaco.xl, paddingTop: espaco.xxl }}>
          <Texto variante="titulo">
            {cadastro ? "Criar conta" : "Entrar"}
          </Texto>

          <FasesDaEntrada estado={estado} aplicar={aplicar} autenticar={sessao.autenticar}>
            <>
              <Card>
                <View style={{ gap: espaco.lg }}>
                  <Input
                    rotulo={leitura ? "Usuário" : "E-mail"}
                    icone={leitura ? "Eye" : "Envelope"}
                    value={email}
                    onChangeText={digitar(setEmail)}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType={leitura ? "default" : "email-address"}
                    autoComplete={leitura ? "username" : "email"}
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
                    {leitura ? null : (
                      <Button
                        rotulo="Esqueci a senha"
                        variante="link"
                        desativado={ocupado}
                        onPress={() => router.push("/esqueci-senha")}
                      />
                    )}
                  </View>
                  <Button
                    rotulo="Entrar"
                    tamanho="L"
                    testID="entrar-enviar"
                    carregando={enviando}
                    desativado={google || apple || !email.trim() || !senha}
                    onPress={() => {
                      // Fase "enviando" aplicada AQUI, antes de `tocar()`: sem
                      // isto o busy só aparecia quando a resposta já tivesse
                      // chegado — tarde demais para desativar campo/botões
                      // durante a espera de verdade (B1). A guarda de UMA
                      // requisição continua sendo o `pendentes` de `tocar()`.
                      aplicar({ fase: "enviando" });
                      void tocar(() => enviar(email, senha, leitura ? sessao.autenticarLeitor : sessao.autenticar, leitura), aplicar);
                    }}
                  />
                </View>
              </Card>

              {leitura ? null : (
                <>
                  <Ou />
                  <BotoesSociais estado={estado} aplicar={aplicar} autenticar={sessao.autenticar} bloqueado={enviando} />
                </>
              )}
              <Button
                rotulo={leitura ? "Entrar com e-mail e senha" : "Entrar com acesso de visualização"}
                variante="link"
                desativado={ocupado}
                onPress={alternarLeitura}
              />
            </>
          </FasesDaEntrada>
        </View>
      </Screen>
    </KeyboardAvoidingView>
  );
}
