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

  // Aplica a fase "verificando" ANTES de chamar `tocar()`, não depois: sem
  // isso, o busy só aparecia quando a promise da requisição já tivesse
  // resolvido — tarde demais para desativar campo e botões durante a espera
  // de verdade (B1). A guarda de UMA requisição continua sendo o `emVoo` de
  // `tocar()`/`entrar.ts`; aplicar de novo aqui num toque redundante é
  // inofensivo (mesmo valor, já é o estado corrente).
  const enviar = (valor: string) => {
    aplicar({ fase: "verificando", desafio, email, modo });
    void tocar(() => verificar(desafio, email, modo, valor, autenticar), aplicar);
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Input
        rotulo={modo === "totp" ? "Código de 6 dígitos" : "Código de backup"}
        icone="Lock"
        value={codigo}
        // Backup: SEM maxLength nativo, no formato "XXXXX-XXXXX" de
        // `db/mfa.py` — um teto de 11 caracteres cortava um colado com um
        // espaço a mais na ponta (" ABCDE-FGHIJ") ou em volta do hífen
        // ("ABCDE - FGHIJ") ANTES de qualquer normalização, perdendo o
        // último caractere e queimando o desafio com um código mutilado. O
        // servidor já normaliza hífen/espaço (em qualquer posição) e caixa —
        // não há necessidade de um teto aqui.
        onChangeText={(v) => {
          // TOTP: mantém só dígitos ASCII (0-9) — descarta espaço, hífen,
          // letra e qualquer separador, inclusive dígito arábico-índico
          // ("١٢٣٤٥٦"): o servidor até aceita a FORMA (Python `isdigit()`
          // conta esses como dígito), mas a comparação do TOTP é contra uma
          // string só de ASCII e nunca bate — deixar passar só queimaria o
          // desafio à toa. Corta em 6 mesmo colando mais, para não mandar o
          // 7º dígito de um autofill ao servidor (400 QUEIMA o desafio —
          // db/mfa.py:351 consome antes de conferir).
          const valor = modo === "totp" ? v.replace(/\D+/g, "").slice(0, TAMANHO_TOTP) : v;
          setCodigo(valor);
          // Auto-envia só quando a ENTRADA EM SI já eram 6 dígitos puros —
          // não o valor FILTRADO. Um colado com lixo ("123-456", "Código:
          // 123456", "G-123456", "123 456") pode virar 6 dígitos DEPOIS do
          // filtro por coincidência, e auto-enviar isso queimaria o desafio
          // com um código que a pessoa nunca digitou por completo. O
          // autofill de SMS entrega uma string só de dígitos — é esse caso
          // que continua disparando sozinho.
          if (modo === "totp" && !verificando && /^[0-9]+$/.test(v) && valor.length === TAMANHO_TOTP) {
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
