import { useEffect, useRef, useState } from "react";
import { View, type TextInput } from "react-native";

import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import type { EstadoCodigoEmail } from "./criarConta";
import { completouTotp, filtrarTotp, TAMANHO_TOTP } from "./totp";

interface Props {
  estado: EstadoCodigoEmail;
  confirmar: (codigo: string) => void;
  reenviar: () => void;
  voltar: () => void;
  /** Voltar a digitar tira o aviso da tentativa anterior. */
  digitou: () => void;
}

/**
 * Fase de código do cadastro — espelho de `CodigoMfa.tsx`, na mesma rota do
 * formulário (a senha fica em memória para o reenvio e nunca vai para a URL).
 * O auto-envio do 6º dígito e o toque em "Confirmar" chamam o MESMO
 * `confirmar`; a guarda de uma requisição só é a da tela (`criar-conta.tsx`).
 */
export function CodigoEmail({ estado, confirmar, reenviar, voltar, digitou }: Props) {
  const [codigo, setCodigo] = useState("");
  const campo = useRef<TextInput>(null);
  const ocupado = estado.fase !== "codigo";
  const aviso = estado.fase === "codigo" ? estado.aviso : undefined;
  const info = estado.fase === "codigo" ? estado.info : undefined;

  // Erro ou reenvio que volta esvazia o campo: com "000000" ainda lá, o
  // próximo dígito seria cortado de volta para "000000" e reenviaria sozinho
  // o código errado. O foco volta porque o `editable={false}` da espera o tira
  // no iOS (#548). Dependência é o `estado` (objeto novo a cada troca), não o
  // texto: dois erros iguais seguidos também limpam.
  useEffect(() => {
    if (estado.fase !== "codigo" || (!estado.aviso && !estado.info)) return;
    setCodigo("");
    campo.current?.focus();
  }, [estado]);

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Confirme seu e-mail</Texto>
      <Texto variante="corpo" tom="inkMuted">
        Enviamos um código de 6 dígitos para {estado.email}.
      </Texto>
      <Input
        ref={campo}
        rotulo="Código de 6 dígitos"
        value={codigo}
        onChangeText={(v) => {
          const valor = filtrarTotp(v);
          setCodigo(valor);
          if (aviso || info) digitou();
          if (!ocupado && completouTotp(v)) confirmar(valor);
        }}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        desativado={ocupado}
        erro={aviso}
      />
      {info ? <Banner mensagem={info} /> : null}
      <Button
        rotulo="Confirmar"
        onPress={() => confirmar(codigo)}
        desativado={ocupado || codigo.length !== TAMANHO_TOTP}
        carregando={estado.fase === "verificando"}
      />
      <Texto variante="legenda" tom="inkMuted">
        Não chegou? Confira o spam. Se este e-mail já tiver conta no PigBank, você recebe um aviso em vez do código.
      </Texto>
      <Button
        rotulo="Reenviar código"
        variante="ghost"
        desativado={ocupado}
        carregando={estado.fase === "reenviando"}
        onPress={reenviar}
      />
      <Button rotulo="Voltar" variante="ghost" icone="ArrowLeft" desativado={ocupado} onPress={voltar} />
    </View>
  );
}
