import { useEffect, useRef, useState } from "react";
import { View, type TextInput } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { espaco } from "@/ui/tokens";

import { alternarModo, tocar, verificar, voltar, type EstadoEntrar, type EstadoMfa, type EstadoVerificando } from "./entrar";
import { completouTotp, filtrarTotp, TAMANHO_TOTP } from "./totp";

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
  const campo = useRef<TextInput>(null);
  const verificando = estado.fase === "verificando";
  const { desafio, email, modo } = estado;
  const aviso = estado.fase === "mfa" ? estado.aviso : undefined;

  // Trocar de modo (TOTP/backup) limpa o campo. `modo` é a dependência: só
  // dispara na TROCA, nunca a cada tecla.
  useEffect(() => {
    setCodigo("");
  }, [modo]);

  // Verificação que volta com erro esvazia o campo TOTP: com "000000" ainda
  // lá, o próximo dígito ("0000001") passaria no filtro, seria cortado de
  // volta para "000000" e reenviaria sozinho o código errado. A dependência é
  // o `estado` (objeto novo a cada `aplicar`), não o `aviso`: dois erros
  // iguais seguidos podem chegar no mesmo commit que o "verificando", e o
  // texto do aviso não muda. Backup fica como foi digitado.
  // O foco volta ao campo nos dois modos: o `editable={false}` da verificação
  // tira o foco no iOS, e sem isto a pessoa tinha de tocar no campo de novo.
  useEffect(() => {
    if (estado.fase !== "mfa" || !estado.aviso) return;
    if (estado.modo === "totp") setCodigo("");
    campo.current?.focus();
  }, [estado]);

  // Aplica a fase "verificando" ANTES de chamar `tocar()`, não depois: sem
  // isso, o busy só aparecia quando a promise da requisição já tivesse
  // resolvido — tarde demais para desativar campo e botões durante a espera
  // de verdade (B1). A guarda de UMA requisição continua sendo o `emVoo` de
  // `tocar()`/`entrar.ts`; aplicar de novo aqui num toque redundante é
  // inofensivo (mesmo valor, já é o estado corrente).
  // Defesa no CAMINHO, não só no `desativado` do botão: TOTP com menos de 6
  // dígitos nunca sai daqui, venha o toque de onde vier (botão, Enter do
  // teclado, um futuro `onSubmitEditing`) — evita gastar uma das 5 tentativas
  // do desafio com um código que o servidor recusaria de qualquer forma
  // (`reserve_login_challenge_attempt` gasta antes de conferir).
  //
  // ponytail: sem teste que discrimine ESTA linha isoladamente — hoje o
  // único chamador é o botão "Verificar", e o `desativado` dele (mesma
  // condição) já impede o toque de chegar aqui com código parcial; a RNTL
  // nem invoca `onPress` de um `Pressable` desativado (medido). A guarda
  // fica porque é o único ponto por onde um `onSubmitEditing`/Enter futuro
  // teria que passar; mexer aqui sem entender isso não vai ver vermelho.
  const enviar = (valor: string) => {
    if (modo === "totp" && valor.length !== TAMANHO_TOTP) return;
    aplicar({ fase: "verificando", desafio, email, modo });
    void tocar(() => verificar(desafio, email, modo, valor, autenticar), aplicar);
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Input
        ref={campo}
        rotulo={modo === "totp" ? "Código de 6 dígitos" : "Código de backup"}
        icone="Lock"
        value={codigo}
        // Backup: SEM maxLength nativo, no formato "XXXXX-XXXXX" de
        // `db/mfa.py` — um teto de 11 caracteres cortava um colado com um
        // espaço a mais na ponta (" ABCDE-FGHIJ") ou em volta do hífen
        // ("ABCDE - FGHIJ") ANTES de qualquer normalização, perdendo o
        // último caractere e gastando uma das 5 tentativas do desafio com um
        // código mutilado. O servidor já normaliza hífen/espaço (em qualquer
        // posição) e caixa — não há necessidade de um teto aqui.
        onChangeText={(v) => {
          // TOTP: a regra de `totp.ts` — só dígitos ASCII, cortado em 6, e
          // auto-envio só quando a entrada crua já eram 6 dígitos puros (cada
          // 400 gasta uma das 5 tentativas do desafio).
          const valor = modo === "totp" ? filtrarTotp(v) : v;
          setCodigo(valor);
          if (modo === "totp" && !verificando && completouTotp(v)) {
            enviar(valor);
          }
        }}
        keyboardType={modo === "totp" ? "number-pad" : "default"}
        textContentType={modo === "totp" ? "oneTimeCode" : undefined}
        autoComplete={modo === "totp" ? "one-time-code" : "off"}
        autoCapitalize="none"
        desativado={verificando}
        erro={aviso}
      />
      <Button
        rotulo="Verificar"
        onPress={() => enviar(codigo)}
        // TOTP só habilita com os 6 dígitos completos — tocar com código
        // parcial (ex.: 3 dígitos) mandava ao servidor e gastava uma das 5
        // tentativas do desafio à toa. Backup mantém a regra de "não vazio"
        // (formato livre).
        desativado={verificando || (modo === "totp" ? codigo.length !== TAMANHO_TOTP : !codigo.trim())}
        carregando={verificando}
      />
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
