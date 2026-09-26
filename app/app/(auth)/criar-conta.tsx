import { router, Stack } from "expo-router";
import { usePreventRemove } from "expo-router/react-navigation";
import { useRef, useState } from "react";
import { KeyboardAvoidingView, Platform, View } from "react-native";

import { CodigoEmail } from "@/features/auth/CodigoEmail";
import {
  LEGENDA_WHATSAPP,
  NOME_MAX,
  SENHA_MIN,
  apagaSenhaNaFase,
  cadastrar,
  confirmar,
  reenviar,
  validar,
  type DadosCadastro,
  type ErrosCadastro,
  type EstadoCriarConta,
} from "@/features/auth/criarConta";
import { GENERICO } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";
import { Termos } from "@/features/auth/Termos";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { Input } from "@/ui/componentes/Input";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

const MENSAGEM_ERRO_COFRE =
  "Sua conta foi criada, mas não conseguimos abrir a sessão neste aparelho. Entre com seu e-mail e senha.";
const LEGENDA_SENHA = `Pelo menos ${SENHA_MIN} caracteres.`;

/**
 * Formulário e código na MESMA rota: a senha fica no `useState` para o
 * reenvio e nunca vira parâmetro de rota. A máquina é `criarConta.ts`.
 */
export default function CriarConta() {
  const sessao = useSessao();
  const [estado, setEstado] = useState<EstadoCriarConta>({ fase: "formulario" });
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [telefone, setTelefone] = useState("");
  const [senha, setSenha] = useState("");
  const [erros, setErros] = useState<ErrosCadastro>({});
  const [avisoLink, setAvisoLink] = useState(false);
  // Uma ação por vez, por `ref` (padrão do `EsqueciSenha`): dois toques no
  // mesmo frame, ou o 6º dígito junto com "Confirmar", chegam aqui antes do
  // re-render — e cada register gasta 1 das 3 tentativas por hora.
  const emVoo = useRef(false);

  const dados: DadosCadastro = { nome, email, telefone, senha };
  const ocupado = estado.fase === "enviando";
  const aviso = estado.fase === "formulario" ? estado.aviso : undefined;
  // Com o verify em voo, sair da rota deixaria a confirmação terminar em
  // segundo plano, sem ninguém para mostrar o resultado. `usePreventRemove`
  // segura o Voltar do Android e qualquer `goBack`; o `gestureEnabled` impede
  // o gesto de voltar do iOS de começar. Mesmo par de `prenderSheet.ts`.
  const verificando = estado.fase === "verificando";
  usePreventRemove(verificando, () => undefined);

  // Por que a senha fica ou sai em cada fase: `apagaSenhaNaFase` (criarConta.ts).
  const aplicar = (e: EstadoCriarConta) => {
    if (apagaSenhaNaFase(e.fase)) setSenha("");
    setAvisoLink(false);
    setEstado(e);
  };

  const agir = (enquanto: EstadoCriarConta, acao: () => Promise<EstadoCriarConta | null>) => {
    if (emVoo.current) return;
    emVoo.current = true;
    aplicar(enquanto);
    // Rejeição inesperada volta à fase de antes, com o aviso genérico — senão
    // `emVoo` ficaria preso em `true` e a tela, travada em "enviando".
    void acao()
      .catch((): EstadoCriarConta =>
        "email" in enquanto ? { fase: "codigo", email: enquanto.email, aviso: GENERICO } : { fase: "formulario", aviso: GENERICO },
      )
      .then((proximo) => {
        emVoo.current = false;
        if (proximo) aplicar(proximo);
      });
  };

  const digitar = (campo: keyof DadosCadastro, definir: (v: string) => void) => (v: string) => {
    definir(v);
    setAvisoLink(false);
    if (erros[campo]) setErros({ ...erros, [campo]: undefined });
    if (aviso) setEstado({ fase: "formulario" });
  };

  const criar = () => {
    const encontrados = validar(dados);
    setErros(encontrados);
    if (Object.keys(encontrados).length > 0) return;
    agir({ fase: "enviando" }, () => cadastrar(dados));
  };

  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <Screen>
        <Stack.Screen options={{ gestureEnabled: !verificando }} />
        <View style={{ gap: espaco.xl, paddingTop: espaco.xxl }}>
          <Texto variante="titulo">Criar conta</Texto>

          {estado.fase === "erro-cofre" ? (
            <Banner
              tom="danger"
              mensagem={MENSAGEM_ERRO_COFRE}
              acao={{ rotulo: "Ir para Entrar", onPress: () => router.replace("/entrar") }}
            />
          ) : estado.fase === "codigo" || estado.fase === "verificando" || estado.fase === "reenviando" ? (
            <CodigoEmail
              estado={estado}
              confirmar={(codigo) =>
                agir({ fase: "verificando", email: estado.email }, () => confirmar(estado.email, codigo, sessao.autenticar))
              }
              reenviar={() => agir({ fase: "reenviando", email: estado.email }, () => reenviar(dados))}
              voltar={() => aplicar({ fase: "formulario" })}
              digitou={() => setEstado({ fase: "codigo", email: estado.email })}
            />
          ) : (
            <>
              <Card>
                <View style={{ gap: espaco.lg }}>
                  <Input
                    rotulo="Nome"
                    value={nome}
                    onChangeText={digitar("nome", setNome)}
                    autoCapitalize="words"
                    autoComplete="name"
                    textContentType="name"
                    maxLength={NOME_MAX}
                    desativado={ocupado}
                    erro={erros.nome}
                  />
                  <Input
                    rotulo="E-mail"
                    icone="Envelope"
                    value={email}
                    onChangeText={digitar("email", setEmail)}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="email-address"
                    autoComplete="email"
                    textContentType="username"
                    desativado={ocupado}
                    erro={erros.email}
                  />
                  <View>
                    <Input
                      rotulo="WhatsApp"
                      value={telefone}
                      onChangeText={digitar("telefone", setTelefone)}
                      keyboardType="phone-pad"
                      textContentType="telephoneNumber"
                      autoComplete="tel"
                      placeholder="(11) 99999-9999"
                      accessibilityHint={LEGENDA_WHATSAPP}
                      desativado={ocupado}
                      erro={erros.telefone}
                    />
                    <Texto variante="legenda" tom="inkMuted">
                      {LEGENDA_WHATSAPP}
                    </Texto>
                  </View>
                  <View>
                    <Input
                      rotulo="Senha"
                      icone="Lock"
                      value={senha}
                      onChangeText={digitar("senha", setSenha)}
                      secureTextEntry
                      autoComplete="new-password"
                      textContentType="newPassword"
                      accessibilityHint={LEGENDA_SENHA}
                      onSubmitEditing={criar}
                      desativado={ocupado}
                      erro={erros.senha}
                    />
                    <Texto variante="legenda" tom="inkMuted">
                      {LEGENDA_SENHA}
                    </Texto>
                  </View>
                  {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
                  <Button rotulo="Criar conta" tamanho="L" carregando={ocupado} onPress={criar} />
                  <Termos aviso={avisoLink} definirAviso={setAvisoLink} />
                </View>
              </Card>

              <View style={{ alignItems: "center" }}>
                <Texto variante="legenda" tom="inkMuted">
                  Já tem conta?
                </Texto>
                <Button rotulo="Entrar" variante="ghost" desativado={ocupado} onPress={() => router.back()} />
              </View>
            </>
          )}
        </View>
      </Screen>
    </KeyboardAvoidingView>
  );
}
