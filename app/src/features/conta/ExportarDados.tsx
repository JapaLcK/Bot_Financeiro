import { useEffect, useRef, useState } from "react";
import { View, type TextInput } from "react-native";

import { useFalha } from "@/features/seguranca/falha";
import { exportarDados } from "@/services/conta";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Icone } from "@/ui/componentes/Icone";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { ComModo } from "./ComModo";
import { campoDoErro } from "./exclusao";

/**
 * Exportar: o servidor manda o link do ZIP por e-mail (15 min, uso único); o app não baixa nada.
 * Uma requisição por vez (`ref`, o teto é 3/h). Conta sem senha (Google/Apple) só toca o botão.
 */
function Formulario({ modo }: { modo: "senha" | "codigo" }) {
  const falha = useFalha();
  const [senha, setSenha] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [enviadoPara, setEnviadoPara] = useState<string | null>(null);
  const [recusada, setRecusada] = useState(0);
  const emVoo = useRef(false);
  const campoSenha = useRef<TextInput>(null);

  useEffect(() => {
    if (recusada) campoSenha.current?.focus();
  }, [recusada]);

  const enviar = async () => {
    if (emVoo.current) return;
    emVoo.current = true;
    setAviso(null);
    setEnviando(true);
    try {
      const r = await exportarDados(modo === "senha" ? senha : undefined);
      setEnviadoPara(r.email_mascarado ?? "seu e-mail");
      // Sem reabrir a guarda: o botão sai da tela com o sucesso.
    } catch (e) {
      emVoo.current = false;
      const texto = falha(e);
      if (texto === null) return;
      setAviso(texto);
      if (campoDoErro(e) === "senha") {
        setSenha("");
        setRecusada((n) => n + 1);
      }
    } finally {
      setEnviando(false);
    }
  };

  if (enviadoPara) {
    return (
      <View style={{ gap: espaco.lg, alignItems: "flex-start" }}>
        <Icone nome="CheckCircle" tamanho={24} tom="positive" />
        <Texto variante="corpo">Enviado para {enviadoPara}. Vale 15 min.</Texto>
      </View>
    );
  }
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Exportar meus dados</Texto>
      {modo === "senha" && (
        <Input
          ref={campoSenha}
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
      )}
      {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
      <Button rotulo="Enviar para meu e-mail" onPress={() => void enviar()} desativado={(modo === "senha" && !senha) || enviando} carregando={enviando} />
    </View>
  );
}

export function ExportarDados() {
  return <ComModo acao="exportar seus dados">{(_conta, modo) => <Formulario modo={modo} />}</ComModo>;
}
