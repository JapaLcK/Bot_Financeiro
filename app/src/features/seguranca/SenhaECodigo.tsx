import { useEffect, useRef, useState } from "react";
import { View, type TextInput } from "react-native";

import { ErroDeApi } from "@/api/client";
import { filtrarTotp, TAMANHO_TOTP } from "@/features/auth/totp";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { useFalha } from "./falha";

interface Props {
  titulo: string;
  texto: string;
  /** Só TOTP (6 dígitos, filtrado) ou TOTP OU código de backup (texto livre). */
  soTotp: boolean;
  rotuloBotao: string;
  varianteBotao?: "primary" | "danger";
  /** Pergunta antes de enviar; `false` desiste sem chamar a rota. */
  confirmar?: () => Promise<boolean>;
  /** Chama a rota; lança para mostrar o erro. */
  enviar: (senha: string, codigo: string) => Promise<void>;
}

/**
 * Senha + código, o formulário de "Gerar novos códigos" e de "Desativar".
 * Sem auto-envio: são dois campos, e o 6º dígito não quer dizer que a senha
 * está pronta.
 *
 * Uma requisição por vez (guarda por `ref`, padrão do `EsqueciSenha`), e a
 * guarda vale desde ANTES da confirmação: dois toques não abrem dois alertas.
 * Ela só reabre na falha ou na desistência — no sucesso a tela troca ou fecha.
 *
 * 400 é sempre o código (a senha errada é 401): o campo esvazia e o foco
 * volta a ele, como no `CodigoMfa` do login.
 */
export function SenhaECodigo({ titulo, texto, soTotp, rotuloBotao, varianteBotao = "primary", confirmar, enviar }: Props) {
  const falha = useFalha();
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [codigoRecusado, setCodigoRecusado] = useState(0);
  const emVoo = useRef(false);
  const campoCodigo = useRef<TextInput>(null);

  // Depois do render que devolveu `editable` ao campo — antes dele o foco não pega.
  useEffect(() => {
    if (codigoRecusado) campoCodigo.current?.focus();
  }, [codigoRecusado]);

  const completo = !!senha && (soTotp ? codigo.length === TAMANHO_TOTP : !!codigo.trim());

  const tocar = async () => {
    if (emVoo.current || !completo) return;
    emVoo.current = true;
    if (confirmar && !(await confirmar())) {
      emVoo.current = false;
      return;
    }
    setAviso(null);
    setEnviando(true);
    try {
      await enviar(senha, codigo);
    } catch (e) {
      emVoo.current = false;
      setEnviando(false);
      const texto = falha(e);
      if (texto === null) return;
      setAviso(texto);
      if (e instanceof ErroDeApi && e.status === 400) {
        setCodigo("");
        setCodigoRecusado((n) => n + 1);
      }
    }
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">{titulo}</Texto>
      <Texto variante="corpo" tom="inkMuted">
        {texto}
      </Texto>
      <Input
        rotulo="Senha"
        icone="Lock"
        value={senha}
        onChangeText={setSenha}
        secureTextEntry
        autoComplete="current-password"
        textContentType="password"
        autoCapitalize="none"
        desativado={enviando}
      />
      <Input
        ref={campoCodigo}
        rotulo={soTotp ? "Código de 6 dígitos" : "Código do app ou de backup"}
        value={codigo}
        onChangeText={(v) => setCodigo(soTotp ? filtrarTotp(v) : v)}
        keyboardType={soTotp ? "number-pad" : "default"}
        textContentType={soTotp ? "oneTimeCode" : undefined}
        autoComplete={soTotp ? "one-time-code" : "off"}
        autoCapitalize="characters"
        autoCorrect={false}
        desativado={enviando}
      />
      {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
      <Button
        rotulo={rotuloBotao}
        variante={varianteBotao}
        onPress={() => void tocar()}
        desativado={!completo || enviando}
        carregando={enviando}
      />
    </View>
  );
}
