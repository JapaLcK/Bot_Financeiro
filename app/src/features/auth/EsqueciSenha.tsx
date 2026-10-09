import { useRef, useState } from "react";
import { View } from "react-native";
import { z } from "zod";

import { ErroDeApi, chamar, comLimite } from "@/api/client";
import { textoDaFalha } from "@/features/auth/entrar";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

const MENSAGEM_NEUTRA =
  "Se o e-mail estiver cadastrado, enviamos um link para redefinir a senha.";

/**
 * `/auth/forgot-password` SEMPRE responde 200 com uma mensagem neutra (o
 * backend não revela se o e-mail existe — CLAUDE.md, isolamento de conta). O
 * app repete essa neutralidade: em qualquer 200, mostra o MESMO texto fixo,
 * nunca o `message` que o servidor devolveu. Falha de rede/limite (429/5xx) é
 * outra categoria — não é sobre revelar conta, e mostra erro de verdade com
 * chance de tentar de novo.
 */
type Estado = { fase: "formulario" } | { fase: "enviando" } | { fase: "enviado" } | { fase: "erro"; mensagem: string };

/**
 * `emailFixo`: modo "definir senha" da Segurança (conta do Google/Apple sem
 * senha). Sem campo — o link vai para o e-mail da própria conta — e o resto do
 * fluxo (POST, guarda, texto neutro) é este mesmo.
 */
export function EsqueciSenha({ emailFixo }: { emailFixo?: string } = {}) {
  const [digitado, setEmail] = useState("");
  const email = emailFixo ?? digitado;
  const [estado, setEstado] = useState<Estado>({ fase: "formulario" });
  // Guarda por `ref`, mesmo padrão do `saindoEmVoo` de `sessao.tsx`: dois
  // toques no MESMO frame chamam `enviar()` duas vezes antes de o
  // `setEstado({fase:"enviando"})` do primeiro chegar a re-renderizar (o
  // backend limita 3/h — dois POSTs por um toque duplo custam 2 dessas 3).
  const emVoo = useRef(false);

  const enviar = async () => {
    if (emVoo.current) return;
    emVoo.current = true;
    setEstado({ fase: "enviando" });
    try {
      await chamar("/auth/forgot-password", z.unknown(), {
        metodo: "POST",
        corpo: { email: email.trim() },
        semAuth: true,
        // Sem isto, um `fetch` pendurado (Android sem timeout — mesmo caso de
        // `sair()`/`services/auth.ts`) deixava `emVoo` preso em `true` para
        // sempre: a sheet ficava em "enviando" e o botão nunca reabria.
        sinal: comLimite(),
      });
      setEstado({ fase: "enviado" });
      // Sem reabrir a guarda aqui: a fase "enviado" substitui o formulário
      // inteiro (nem o botão continua na árvore), não há um segundo toque a
      // temer.
    } catch (e) {
      emVoo.current = false;
      const limite = emailFixo !== undefined && e instanceof ErroDeApi && e.status === 429;
      setEstado({ fase: "erro", mensagem: limite ? "Você já pediu o link várias vezes. Tente de novo em 1 hora." : textoDaFalha(e) });
    }
  };

  if (estado.fase === "enviado") {
    return (
      <View style={{ gap: espaco.lg }}>
        <Texto variante="secao">Verifique seu e-mail</Texto>
        <Texto variante="corpo" tom="inkMuted">
          {MENSAGEM_NEUTRA}
          {emailFixo !== undefined && " Abra o link no navegador e volte ao app."}
        </Texto>
      </View>
    );
  }

  const enviando = estado.fase === "enviando";
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">{emailFixo === undefined ? "Esqueci a senha" : "Definir senha"}</Texto>
      <Texto variante="corpo" tom="inkMuted">
        {emailFixo === undefined
          ? "Digite seu e-mail: se ele estiver cadastrado, enviamos um link para redefinir a senha."
          : `Sua conta ainda não tem senha. Vamos enviar um link para ${emailFixo}: abra no navegador, crie a senha e volte ao app.`}
      </Texto>
      {emailFixo === undefined && (
        <Input
          rotulo="E-mail"
          icone="Envelope"
          value={email}
          onChangeText={setEmail}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          autoComplete="email"
          textContentType="username"
          desativado={enviando}
          testID="esqueci-email"
        />
      )}
      {estado.fase === "erro" ? <Banner tom="danger" mensagem={estado.mensagem} /> : null}
      <Button rotulo={emailFixo === undefined ? "Enviar" : "Enviar link"} onPress={() => void enviar()} desativado={!email.trim() || enviando} carregando={enviando} />
    </View>
  );
}
