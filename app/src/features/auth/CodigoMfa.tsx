import { useEffect, useState } from "react";
import { View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { espaco } from "@/ui/tokens";

import { alternarModo, tocar, verificar, voltar, type EstadoEntrar, type EstadoMfa, type EstadoVerificando } from "./entrar";

const TAMANHO_TOTP = 6;

interface Props {
  estado: EstadoMfa | EstadoVerificando;
  autenticar: () => void;
  aplicar: (e: EstadoEntrar) => void;
}

/**
 * Fase de código do MFA — mesma rota de `entrar.tsx`, nunca uma rota própria:
 * o desafio é uma credencial de 5 minutos, e ir por parâmetro de rota
 * (histórico, deep link) o exporia onde não precisa.
 *
 * O auto-envio do 6º dígito e o toque em "Verificar" chamam o MESMO `enviar`,
 * que passa pelo `tocar()` de `entrar.ts` — é essa guarda, não uma daqui, que
 * garante UMA requisição só quando os dois coincidem.
 */
export function CodigoMfa({ estado, autenticar, aplicar }: Props) {
  const [codigo, setCodigo] = useState("");
  const verificando = estado.fase === "verificando";
  const { desafio, email, modo } = estado;
  const aviso = estado.fase === "mfa" ? estado.aviso : undefined;

  // Trocar de modo (TOTP/backup) limpa o campo. `modo` é a dependência: só
  // dispara na TROCA, nunca a cada tecla.
  useEffect(() => {
    setCodigo("");
  }, [modo]);

  const enviar = (valor: string) => {
    void tocar(() => verificar(desafio, email, modo, valor, autenticar), aplicar);
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Input
        rotulo={modo === "totp" ? "Código de 6 dígitos" : "Código de backup"}
        icone="Lock"
        value={codigo}
        onChangeText={(v) => {
          setCodigo(v);
          // Só o modo TOTP auto-envia: código de backup não tem tamanho fixo.
          if (modo === "totp" && !verificando && v.replace(/\s+/g, "").length === TAMANHO_TOTP) {
            enviar(v);
          }
        }}
        keyboardType={modo === "totp" ? "number-pad" : "default"}
        textContentType={modo === "totp" ? "oneTimeCode" : undefined}
        autoComplete={modo === "totp" ? "one-time-code" : "off"}
        autoCapitalize="none"
        desativado={verificando}
        erro={aviso}
      />
      <Button rotulo="Verificar" onPress={() => enviar(codigo)} desativado={!codigo.trim() || verificando} carregando={verificando} />
      <Button
        rotulo={modo === "totp" ? "Usar código de backup" : "Usar código do aplicativo"}
        variante="ghost"
        desativado={verificando}
        onPress={() => aplicar(alternarModo(estado.fase === "mfa" ? estado : { fase: "mfa", desafio, email, modo }))}
      />
      <Button rotulo="Voltar" variante="ghost" icone="ArrowLeft" desativado={verificando} onPress={() => aplicar(voltar())} />
    </View>
  );
}
