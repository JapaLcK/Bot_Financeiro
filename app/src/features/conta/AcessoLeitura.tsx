import { useEffect, useRef, useState } from "react";
import { View } from "react-native";

import { filtrarTotp, TAMANHO_TOTP } from "@/features/auth/totp";
import { useFalha } from "@/features/seguranca/falha";
import { acessoLeituraLiberado, definirLeitura, desativarLeitura, lerCredencialLeitura, pedirCodigoLeitura } from "@/services/conta";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { textoDaLeitura } from "./leitura";

type Modo = "criar" | "trocar" | "desativar";
type Credencial = { ativo: boolean; usuario?: string | null };

/**
 * "Acesso de visualização" (Segurança): atrás do freio `acesso_leitura` do `/api/app/me` (`ACESSO_LEITURA_USER_IDS`); leitor e quem está fora da lista
 * não veem nada (falha de leitura do freio = sem a seção). Criar, trocar a senha e desativar pedem o código de 6 dígitos que o servidor manda ao e-mail
 * do dono (só no toque em "Enviar código"). A senha vive só no estado do formulário e nunca volta do servidor.
 */
export function AcessoLeitura() {
  const falha = useFalha();
  const [liberado, setLiberado] = useState(false);
  const [cred, setCred] = useState<Credencial | null>(null);
  const [modo, setModo] = useState<Modo | null>(null);
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
  const [confirma, setConfirma] = useState("");
  const [codigo, setCodigo] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [aviso, setAviso] = useState<{ tom: "danger" | "info"; texto: string } | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const voando = useRef(false);

  useEffect(() => {
    let vivo = true;
    void acessoLeituraLiberado()
      .then(async (ok) => (ok ? await lerCredencialLeitura() : null))
      .then((c) => { if (vivo && c) { setCred(c); setLiberado(true); } })
      .catch(() => undefined);
    return () => { vivo = false; };
  }, []);

  if (!liberado || !cred) return null;

  const abrir = (m: Modo) => {
    setModo(m); setUsuario(m === "trocar" ? (cred.usuario ?? "") : ""); setSenha(""); setConfirma(""); setCodigo(""); setEnviado(false); setAviso(null);
  };
  const rodar = async (acao: () => Promise<void>, reserva: string) => {
    if (voando.current) return;
    voando.current = true;
    setOcupado(true);
    setAviso(null);
    try { await acao(); } catch (e) {
      const texto = textoDaLeitura(e) ?? falha(e);
      if (texto !== null) setAviso({ tom: "danger", texto: texto === "Não foi possível completar a ação." ? reserva : texto });
      setCodigo("");
    } finally { voando.current = false; setOcupado(false); }
  };
  const pedir = () => rodar(async () => { await pedirCodigoLeitura(); setEnviado(true); }, "Não foi possível enviar o código. Tente de novo.");
  const enviar = () => {
    if (modo !== "desativar" && senha !== confirma) return setAviso({ tom: "danger", texto: "As senhas não são iguais." });
    return rodar(async () => {
      const r = modo === "desativar" ? await desativarLeitura(codigo) : await definirLeitura(usuario.trim(), senha, codigo);
      setCred(r);
      setModo(null);
      setAviso({ tom: "info", texto: modo === "desativar" ? "Acesso de visualização desativado." : "Acesso de visualização salvo." });
    }, modo === "desativar" ? "Não foi possível desativar. Tente de novo." : "Não foi possível salvar. Tente de novo.");
  };
  const completo = codigo.length === TAMANHO_TOTP && (modo === "desativar" || (!!usuario.trim() && !!senha && !!confirma));

  return (
    <View style={{ gap: espaco.md }}>
      <Texto variante="secao">Acesso de visualização</Texto>
      <Texto variante="corpo" tom="inkMuted">Quem entra com este usuário vê a conta inteira e não consegue lançar, editar nem pagar.</Texto>
      {modo === null && (cred.ativo ? (
        <>
          <Texto variante="corpo">Usuário: {cred.usuario}</Texto>
          <Button rotulo="Trocar senha" variante="secondary" onPress={() => abrir("trocar")} />
          <Button rotulo="Desativar acesso" variante="ghost" onPress={() => abrir("desativar")} />
        </>
      ) : (
        <Button rotulo="Criar acesso" onPress={() => abrir("criar")} />
      ))}
      {modo !== null && (
        <View style={{ gap: espaco.md }}>
          {modo !== "desativar" && (
            <>
              <Input rotulo="Usuário" value={usuario} onChangeText={setUsuario} autoCapitalize="none" autoCorrect={false} desativado={ocupado} />
              <Input rotulo={modo === "trocar" ? "Nova senha" : "Senha"} value={senha} onChangeText={setSenha} secureTextEntry autoCapitalize="none" textContentType="newPassword" desativado={ocupado} />
              <Input rotulo="Confirmar senha" value={confirma} onChangeText={setConfirma} secureTextEntry autoCapitalize="none" textContentType="newPassword" desativado={ocupado} />
            </>
          )}
          <Button rotulo={enviado ? "Enviar outro código" : "Enviar código para meu e-mail"} variante="secondary" onPress={() => void pedir()} desativado={ocupado} />
          {enviado && <Texto variante="legenda" tom="inkMuted">Código enviado. Confira seu e-mail.</Texto>}
          <Input rotulo="Código do e-mail" value={codigo} onChangeText={(v) => setCodigo(filtrarTotp(v))} keyboardType="number-pad" textContentType="oneTimeCode" autoCorrect={false} desativado={ocupado} />
          <Button rotulo={modo === "desativar" ? "Desativar" : "Salvar"} variante={modo === "desativar" ? "danger" : "primary"} onPress={() => void enviar()} desativado={!completo || ocupado} carregando={ocupado} />
          <Button rotulo="Cancelar" variante="ghost" onPress={() => setModo(null)} desativado={ocupado} />
        </View>
      )}
      {aviso && <Banner tom={aviso.tom} mensagem={aviso.texto} />}
    </View>
  );
}
