import { useState } from "react";
import { View } from "react-native";

import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Card } from "@/ui/componentes/Card";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { LEGENDA_WHATSAPP, NOME_MAX, validarPerfil, type ErrosCadastro } from "./criarConta";
import { tocar, type EstadoEntrar } from "./entrar";
import { criarContaComGoogle } from "./google";
import { Termos } from "./Termos";

interface Props {
  estado: Extract<EstadoEntrar, { fase: "google-cadastro" | "google-criando" }>;
  autenticar: () => void;
  aplicar: (e: EstadoEntrar) => void;
}

/**
 * Fases C e K: quem entrou pelo Google sem conta completa o cadastro na MESMA
 * rota Entrar (o token do pré-cadastro nunca vira parâmetro de rota). E-mail
 * só leitura, nome sugerido pelo Google, WhatsApp obrigatório. Montado nas
 * duas fases no mesmo lugar da árvore: o que foi digitado sobrevive a K → C.
 */
export function CompletarCadastroGoogle({ estado, autenticar, aplicar }: Props) {
  const [nome, setNome] = useState("nome" in estado ? estado.nome : "");
  const [telefone, setTelefone] = useState("");
  const [erros, setErros] = useState<ErrosCadastro>({});
  const [avisoLink, setAvisoLink] = useState(false);
  const criando = estado.fase === "google-criando";
  const aviso = estado.fase === "google-cadastro" ? estado.aviso : undefined;

  const digitar = (campo: "nome" | "telefone", definir: (v: string) => void) => (v: string) => {
    definir(v);
    setAvisoLink(false);
    if (erros[campo]) setErros({ ...erros, [campo]: undefined });
    if (estado.fase === "google-cadastro" && aviso) aplicar({ ...estado, aviso: undefined });
  };

  const criar = () => {
    const encontrados = validarPerfil({ nome, telefone });
    setErros(encontrados);
    setAvisoLink(false);
    if (Object.keys(encontrados).length > 0) return;
    const { token, email } = estado;
    aplicar({ fase: "google-criando", token, email });
    void tocar(() => criarContaComGoogle({ token, email, nome }, telefone, autenticar), aplicar);
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="corpo" tom="inkMuted">
        Sua conta Google ainda não tem PigBank. Confira seu nome e informe seu WhatsApp para criar a conta.
      </Texto>
      <Card>
        <View style={{ gap: espaco.lg }}>
          <Input rotulo="E-mail" icone="Envelope" value={estado.email} desativado />
          <Input
            rotulo="Nome"
            value={nome}
            onChangeText={digitar("nome", setNome)}
            autoCapitalize="words"
            autoComplete="name"
            textContentType="name"
            maxLength={NOME_MAX}
            desativado={criando}
            erro={erros.nome}
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
              desativado={criando}
              erro={erros.telefone}
            />
            <Texto variante="legenda" tom="inkMuted">
              {LEGENDA_WHATSAPP}
            </Texto>
          </View>
          {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
          <Button rotulo="Criar conta" tamanho="L" carregando={criando} onPress={criar} />
          <Termos aviso={avisoLink} definirAviso={setAvisoLink} />
        </View>
      </Card>
      {/* Transição LOCAL: nunca `voltar()`/`abandonarEntrada()`, que superariam
          um cadastro em voo em outra rota (#592). */}
      <Button
        rotulo="Voltar"
        variante="ghost"
        icone="ArrowLeft"
        desativado={criando}
        onPress={() => aplicar({ fase: "formulario" })}
      />
    </View>
  );
}
