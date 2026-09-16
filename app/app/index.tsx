import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  useColorScheme,
} from "react-native";

import { abrir, entrarNaTela, montar, sairNaTela, tocar, type Estado } from "@/ui/inicio";
import { claro, escuro, espaco, raio, texto } from "@/ui/tokens";

/**
 * A prova de ponta a ponta da Fase 1: entrada por e-mail → `Authorization:
 * Bearer` → `/auth/me` → "Olá, {nome}" → Sair. Não é produto; a primeira tela
 * de verdade é a Fase 3. A lógica (e as corridas) mora em `@/ui/inicio`, que o
 * Jest exercita; aqui só se desenha o estado.
 */
export default function Inicio() {
  const paleta = useColorScheme() === "dark" ? escuro : claro;
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");

  // `montar`, não `tocar`: com ação em voo, `tocar` seria ignorado e a tela nova ficaria em
  // `carregando` para sempre. Isto e os `tocar` dos botões não têm teste: trocar deixa a suíte verde.
  useEffect(() => {
    void montar(setEstado);
  }, []);

  // `inkMuted`, não `border`: o contorno do campo precisa de 3:1 (WCAG 1.4.11).
  // `border` dá 1,25:1 no claro e 1,35:1 no escuro; `inkMuted` dá 5,84 e 6,50
  // sobre `surface`, e 6,30 e 7,01 sobre `bg`.
  const campo = [
    estilos.campo,
    { borderColor: paleta.inkMuted, color: paleta.ink, backgroundColor: paleta.surface },
  ];

  // O e-mail sai junto: o formulário depois do Sair não entrega a conta anterior.
  const sairDaConta = () => {
    setEmail("");
    void tocar(sairNaTela, setEstado);
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={[estilos.tela, { backgroundColor: paleta.bg }]}
    >
      {estado.fase === "carregando" && (
        <ActivityIndicator color={paleta.brand} accessibilityLabel="Carregando" />
      )}

      {estado.fase === "entrada" && (
        <>
          <Text style={[texto.titulo, { color: paleta.ink }]}>Entre para continuar.</Text>
          <TextInput
            accessibilityLabel="E-mail"
            placeholder="E-mail"
            placeholderTextColor={paleta.inkMuted}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            autoComplete="email"
            textContentType="username"
            value={email}
            onChangeText={setEmail}
            style={campo}
          />
          <TextInput
            accessibilityLabel="Senha"
            placeholder="Senha"
            placeholderTextColor={paleta.inkMuted}
            secureTextEntry
            autoComplete="current-password"
            textContentType="password"
            value={senha}
            onChangeText={setSenha}
            style={campo}
          />
          {estado.aviso ? (
            <Text
              accessibilityLiveRegion="polite"
              style={[texto.corpo, { color: paleta.negative }]}
            >
              {estado.aviso}
            </Text>
          ) : null}
          <Botao
            rotulo="Entrar"
            paleta={paleta}
            desativado={!email.trim() || !senha}
            onPress={() => {
              // A senha sai da tela antes da requisição: voltar ao formulário
              // nunca a mostra preenchida.
              const s = senha;
              setSenha("");
              void tocar(() => entrarNaTela(email, s), setEstado);
            }}
          />
        </>
      )}

      {estado.fase === "pronto" && (
        <>
          <Text style={[texto.titulo, { color: paleta.ink }]}>Olá, {estado.nome}</Text>
          <Botao rotulo="Sair" paleta={paleta} onPress={sairDaConta} />
        </>
      )}

      {estado.fase === "erro" && (
        <>
          <Text style={[texto.corpo, { color: paleta.negative }]}>{estado.mensagem}</Text>
          <Botao
            rotulo="Tentar de novo"
            paleta={paleta}
            onPress={() =>
              void tocar(estado.refazer === "sair" ? sairNaTela : abrir, setEstado)
            }
          />
          {/* Um `/auth/me` que falha sempre (conta agendada para exclusão dá 403
              com a sessão ainda válida) prenderia a pessoa aqui sem saída. */}
          {estado.refazer === "abrir" && (
            <Botao rotulo="Sair" paleta={paleta} onPress={sairDaConta} />
          )}
        </>
      )}
    </KeyboardAvoidingView>
  );
}

/**
 * Fundo `ink` com texto `bg`, e não rosa: branco sobre `#FF2D8E` dá ~3,5:1 e
 * reprova no AA. O rosa fica no spinner.
 */
function Botao(props: {
  rotulo: string;
  paleta: typeof claro | typeof escuro;
  onPress: () => void;
  desativado?: boolean;
}) {
  const { rotulo, paleta, onPress, desativado = false } = props;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: desativado }}
      disabled={desativado}
      onPress={onPress}
      style={({ pressed }) => [
        estilos.botao,
        { backgroundColor: paleta.ink, opacity: desativado ? 0.4 : pressed ? 0.8 : 1 },
      ]}
    >
      <Text style={[texto.corpo, { color: paleta.bg, fontWeight: "600" }]}>{rotulo}</Text>
    </Pressable>
  );
}

const estilos = StyleSheet.create({
  tela: {
    flex: 1,
    justifyContent: "center",
    padding: espaco.lg,
    gap: espaco.md,
  },
  campo: {
    minHeight: 48,
    borderWidth: 1,
    borderRadius: raio.md,
    paddingHorizontal: espaco.md,
    fontSize: texto.corpo.fontSize,
  },
  botao: {
    minHeight: 48,
    borderRadius: raio.md,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: espaco.lg,
  },
});
