import { useEffect, useRef, useState } from "react";
import { Alert, View, type TextInput } from "react-native";

import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { filtrarTotp, TAMANHO_TOTP } from "@/features/auth/totp";
import { useSessao } from "@/features/auth/sessao";
import { apagarLayouts } from "@/features/painel/catalogo";
import { useFalha } from "@/features/seguranca/falha";
import { excluirConta, pedirCodigoReauth } from "@/services/conta";
import { lerCredenciais } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { ComModo } from "./ComModo";
import { campoDoErro, NAO_CONFIRMOU, textoDaExclusao } from "./exclusao";

/** Resolve `true` só no "Excluir"; cancelar ou dispensar o alerta é `false`. */
function confirmar(): Promise<boolean> {
  return new Promise((resolver) =>
    Alert.alert(
      "Excluir a conta?",
      "Não dá para desfazer.",
      [
        { text: "Cancelar", style: "cancel", onPress: () => resolver(false) },
        { text: "Excluir", style: "destructive", onPress: () => resolver(true) },
      ],
      { cancelable: true, onDismiss: () => resolver(false) },
    ),
  );
}

type Campo = "senha" | "codigo" | "totp";
type Status = "deleted" | "deleting" | "superada";

/**
 * Excluir conta: o servidor apaga NA HORA (e revoga as sessões). Uma requisição por vez, com a guarda por `ref`
 * valendo desde ANTES do alerta (dois toques não abrem dois alertas); ela só reabre na falha ou na desistência.
 * A sheet fica presa desde o envio (`prender`): fechar com o DELETE em voo deixaria a resposta sem tela.
 * O código do e-mail só sai no toque em "Enviar código": abrir a sheet não gasta a cota de 3/h.
 * PII: senha e códigos só vão para o corpo da requisição, nunca para log ou analytics.
 */
function Formulario({ modo, userId, mfa, prender, desprender }: { modo: "senha" | "codigo"; userId: number; mfa: boolean; prender: () => void; desprender: () => void }) {
  const falha = useFalha();
  const sessao = useSessao();
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [totp, setTotp] = useState("");
  const [mostrarTotp, setMostrarTotp] = useState(mfa);
  const [enviadoPara, setEnviadoPara] = useState<string | null>(null);
  const [pedindo, setPedindo] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [semSair, setSemSair] = useState<Status | null>(null);
  const [foco, setFoco] = useState<{ campo: Campo; n: number } | null>(null);
  const emVoo = useRef(false);
  const pedindoRef = useRef(false);
  const saindo = useRef(false);
  const campos = { senha: useRef<TextInput>(null), codigo: useRef<TextInput>(null), totp: useRef<TextInput>(null) };

  // Depois do render que devolveu `editable` ao campo (e montou o do autenticador) — antes dele o foco não pega.
  useEffect(() => {
    if (foco) campos[foco.campo].current?.focus();
  }, [foco]);

  const apontar = (campo: Campo, limpar: boolean) => {
    if (campo === "totp") setMostrarTotp(true);
    if (limpar) ({ senha: setSenha, codigo: setCodigo, totp: setTotp })[campo]("");
    setFoco((f) => ({ campo, n: (f?.n ?? 0) + 1 }));
  };

  const completo = (modo === "codigo" || !!senha) && codigo.length === TAMANHO_TOTP && (!mostrarTotp || !!totp.trim());

  const pedirCodigo = async () => {
    if (pedindoRef.current || emVoo.current) return;
    pedindoRef.current = true;
    setPedindo(true);
    setAviso(null);
    try {
      setEnviadoPara((await pedirCodigoReauth()).email_mascarado);
      apontar("codigo", false);
    } catch (e) {
      const texto = falha(e);
      if (texto !== null) setAviso(texto);
    } finally {
      pedindoRef.current = false;
      setPedindo(false);
    }
  };

  const sairEAvisar = async (status: Status) => {
    if (saindo.current) return; // duplo toque no "Sair" do Banner: um aviso só
    saindo.current = true;
    if (!(await sessao.sair())) {
      saindo.current = false;
      setEnviando(false);
      desprender();
      setSemSair(status);
      return;
    }
    if (status === "deleting") Alert.alert("Excluindo", "Você recebe um e-mail ao terminar.");
    else Alert.alert(status === "superada" ? NAO_CONFIRMOU : "Conta excluída");
  };

  const excluir = async () => {
    if (emVoo.current || !completo) return;
    emVoo.current = true;
    if (!(await confirmar())) {
      emVoo.current = false;
      return;
    }
    setAviso(null);
    setEnviando(true);
    prender();
    let status: Status;
    try {
      status = await excluirConta({ ...(modo === "senha" ? { password: senha } : {}), code: codigo, ...(mostrarTotp ? { totp: totp.trim() } : {}) });
    } catch (e) {
      // 401 de sessão na PRÓPRIA requisição: o servidor só revoga sessões no commit e responde 200, então aqui nada
      // foi executado e a conta está viva: os layouts ficam. Timeout e rede também não apagam: a conta pode existir.
      if (e instanceof SessaoExpirada) return void falha(e);
      // Superada: o 200 chegou com a sessão já revogada por requisição paralela (exclusão provável). Só sai se o cofre
      // está vazio: com credenciais de OUTRA conta nele (B entrou durante o DELETE), `sair()` as destruiria.
      if (e instanceof RequisicaoSuperada) {
        await apagarLayouts(userId);
        if ((await lerCredenciais()) === null) await sairEAvisar("superada");
        return;
      }
      emVoo.current = false;
      setEnviando(false);
      desprender();
      const texto = falha(e);
      if (texto === null) return;
      setAviso(textoDaExclusao(e, texto));
      // `mfa_obrigatorio` só revela o campo do autenticador; o código do e-mail segue valendo (a transação desfaz).
      const campo = campoDoErro(e);
      if (campo) apontar(campo, true);
      return;
    }
    // A conta já não existe: layouts do aparelho e sessão saem antes de qualquer aviso.
    await apagarLayouts(userId);
    await sairEAvisar(status);
  };

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Excluir conta</Texto>
      <Texto variante="corpo" tom="inkMuted">
        Apaga tudo agora. Assinatura no cartão é cancelada na hora, sem reembolso.
      </Texto>
      {modo === "senha" && (
        <Input
          ref={campos.senha}
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
      <Input
        ref={campos.codigo}
        rotulo="Código do e-mail"
        value={codigo}
        onChangeText={(v) => setCodigo(filtrarTotp(v))}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        autoCorrect={false}
        desativado={enviando}
      />
      {enviadoPara ? (
        <Texto variante="legenda" tom="inkMuted">
          Enviado para {enviadoPara}
        </Texto>
      ) : null}
      <Button rotulo={enviadoPara ? "Reenviar" : "Enviar código"} variante="secondary" onPress={() => void pedirCodigo()} desativado={pedindo || enviando} carregando={pedindo} />
      {mostrarTotp && (
        <Input
          ref={campos.totp}
          rotulo="Código do app ou de backup"
          value={totp}
          onChangeText={setTotp}
          autoCapitalize="characters"
          autoCorrect={false}
          autoComplete="off"
          desativado={enviando}
        />
      )}
      {aviso ? <Banner tom="danger" mensagem={aviso} /> : null}
      {semSair ? <Banner tom="danger" mensagem="Não conseguimos sair. Tente de novo." acao={{ rotulo: "Sair", onPress: () => void sairEAvisar(semSair) }} /> : null}
      <Button rotulo="Excluir conta" variante="danger" onPress={() => void excluir()} desativado={!completo || enviando || pedindo || !!semSair} carregando={enviando} />
    </View>
  );
}

export function ExcluirConta({ prender, desprender }: { prender: () => void; desprender: () => void }) {
  return (
    <ComModo acao="excluir a conta">
      {(conta, modo) => <Formulario modo={modo} userId={conta.user_id} mfa={conta.mfa_enabled === true} prender={prender} desprender={desprender} />}
    </ComModo>
  );
}
