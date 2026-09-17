import { router } from "expo-router";
import { useState } from "react";
import { View } from "react-native";

import { AmountInput } from "@/ui/componentes/AmountInput";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { ToastProvider, useToast } from "@/ui/componentes/Toast";
import { espaco } from "@/ui/tokens";

function Formulario() {
  const { mostrar } = useToast();
  const [centavos, setCentavos] = useState(0);
  const [descricao, setDescricao] = useState("");
  // Erro só depois que o campo foi tocado (`onBlur`) — mostrar "Preencha a
  // descrição" antes de qualquer interação seria o erro aparecendo antes do
  // usuário ter feito algo (o oposto do estado que ENSINA, impeccable).
  const [tocou, setTocou] = useState(false);
  const erroDescricao = tocou && descricao.length === 0 ? "Preencha a descrição" : undefined;

  return (
    <View style={{ gap: espaco.lg, paddingTop: espaco.lg, paddingBottom: espaco.xxl }}>
      <Texto variante="titulo">Novo lançamento</Texto>

      <AmountInput centavos={centavos} onChange={setCentavos} rotulo="Valor" />
      <Input
        rotulo="Descrição"
        placeholder="Ex.: Mercado"
        value={descricao}
        onChangeText={setDescricao}
        onBlur={() => setTocou(true)}
        erro={erroDescricao}
      />

      <View style={{ gap: espaco.sm }}>
        <Button rotulo="Salvar" onPress={() => mostrar({ mensagem: "Lançamento salvo!", tom: "sucesso" })} />
        <Button rotulo="Cancelar" variante="secondary" onPress={() => router.back()} />
        <Button rotulo="Abrir sheet" variante="ghost" onPress={() => router.push("/_ds/sheet-exemplo")} />
        <Button rotulo="Excluir" variante="danger" onPress={() => mostrar({ mensagem: "Não é possível excluir.", tom: "erro" })} />
      </View>
    </View>
  );
}

/**
 * Tela-modelo: `AmountInput`, `Input` com erro, os 4 botões, `Toast` e
 * `Sheet` juntos — a composição que a Fase 2 pediu como prova final.
 */
export default function TelaFormulario() {
  return (
    // Rolagem LIGADA: é esta tela que prova Dynamic Type 130% sem overflow
    // (título + AmountInput + Input com erro + 4 botões), e um formulário
    // com teclado precisa da rolagem de qualquer forma para não ficar preso
    // atrás do teclado.
    <ToastProvider>
      <Screen>
        <Formulario />
      </Screen>
    </ToastProvider>
  );
}
