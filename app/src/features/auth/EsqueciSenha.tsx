import { useRef, useState } from "react";
import { View } from "react-native";
import { z } from "zod";

import { chamar, comLimite } from "@/api/client";
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

export function EsqueciSenha() {
  const [email, setEmail] = useState("");
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
      setEstado({ fase: "erro", mensagem: textoDaFalha(e) });
    }
  };

  if (estado.fase === "enviado") {
    return (
      <View style={{ gap: espaco.lg }}>
        <Texto variante="secao">Verifique seu e-mail</Texto>
        <Texto variante="corpo" tom="inkMuted">
          {MENSAGEM_NEUTRA}
        </Texto>
      </View>
    );
  }

  const enviando = estado.fase === "enviando";
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Esqueci a senha</Texto>
      <Texto variante="corpo" tom="inkMuted">
        Digite seu e-mail: se ele estiver cadastrado, enviamos um link para redefinir a senha.
      </Texto>
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
      />
      {estado.fase === "erro" ? <Banner tom="danger" mensagem={estado.mensagem} /> : null}
      <Button rotulo="Enviar" onPress={() => void enviar()} desativado={!email.trim() || enviando} carregando={enviando} />
    </View>
  );
}
