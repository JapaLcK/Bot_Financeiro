import { useEffect, useRef, useState } from "react";
import { Linking, View, type TextInput } from "react-native";
import { SvgXml } from "react-native-svg";

import { ErroDeApi } from "@/api/client";
import type { MfaSetup } from "@/api/schemas/auth";
import { completouTotp, filtrarTotp, TAMANHO_TOTP } from "@/features/auth/totp";
import { ativarMfa, iniciarMfa } from "@/services/mfa";
import { deBase64Url } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Input } from "@/ui/componentes/Input";
import { Texto } from "@/ui/componentes/Texto";
import { claro, espaco, raio } from "@/ui/tokens";

import { CodigosBackup } from "./CodigosBackup";
import { useFalha } from "./falha";

const PREFIXO_QR = "data:image/svg+xml;base64,";
const LADO_QR = 184;
/** Cópia do `detail` do 400 em `POST /auth/mfa/enable`; o teste `ativar_mfa` compara as duas (CLAUDE.md §0.7). */
export const SETUP_EXPIRADO = "Inicie o setup primeiro.";

type Setup = MfaSetup & { svg: string };

type Estado =
  | { fase: "senha"; aviso?: string }
  | { fase: "verificando-senha" }
  | { fase: "qr"; setup: Setup; aviso?: string }
  | { fase: "ativando"; setup: Setup }
  | { fase: "codigos"; codigos: string[] }
  | { fase: "sem-codigos" };

interface Props {
  /** O enable saiu: quem chama prende a sheet (os códigos só vêm nesta resposta). */
  aoEnviar: () => void;
  /** O enable falhou: quem chama solta a trava, sem sair. */
  aoFalhar: () => void;
  /** "Já guardei meus códigos". */
  aoConcluir: () => void;
  /** MFA ligado sem os códigos (409): leva a "Gerar novos códigos". */
  aoGerarNovos: () => void;
}

/**
 * senha → QR/segredo → primeiro código → códigos de backup.
 *
 * Uma requisição por vez, pela guarda de `ref` (padrão do `EsqueciSenha`): o
 * setup gasta 1 de 5 por hora e o enable 1 de 10, e dois toques no mesmo
 * frame chegariam antes do `setEstado` re-renderizar.
 */
export function AtivarMfa({ aoEnviar, aoFalhar, aoConcluir, aoGerarNovos }: Props) {
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "senha" });
  const [senha, setSenha] = useState("");
  const [codigo, setCodigo] = useState("");
  const [avisoLink, setAvisoLink] = useState<string | null>(null);
  const emVoo = useRef(false);
  const campoCodigo = useRef<TextInput>(null);

  // Código recusado: com "000000" ainda no campo, o próximo dígito passaria
  // no filtro, seria cortado de volta e reenviaria sozinho o código errado.
  // E o `editable={false}` do "ativando" tira o foco no iOS. Mesmo raciocínio
  // do `CodigoMfa` do login.
  useEffect(() => {
    if (estado.fase !== "qr" || !estado.aviso) return;
    setCodigo("");
    campoCodigo.current?.focus();
  }, [estado]);

  const verificarSenha = async () => {
    if (emVoo.current) return;
    emVoo.current = true;
    setEstado({ fase: "verificando-senha" });
    try {
      const r = await iniciarMfa(senha);
      const setup = { ...r, svg: deBase64Url(r.qr_code.slice(PREFIXO_QR.length)) };
      setSenha("");
      setEstado({ fase: "qr", setup });
    } catch (e) {
      const texto = falha(e);
      setEstado({ fase: "senha", aviso: texto ?? undefined });
    } finally {
      emVoo.current = false;
    }
  };

  const ativar = async (valor: string, setup: Setup) => {
    if (emVoo.current || valor.length !== TAMANHO_TOTP) return;
    emVoo.current = true;
    setEstado({ fase: "ativando", setup });
    aoEnviar();
    try {
      const { backup_codes } = await ativarMfa(valor);
      setEstado({ fase: "codigos", codigos: backup_codes });
    } catch (e) {
      aoFalhar();
      const texto = falha(e);
      // `null` sem sair da tela (resposta de outra conta): volta ao QR, sem aviso.
      if (texto === null) return setEstado({ fase: "qr", setup });
      // 409: o MFA já está ligado — um enable anterior passou e a resposta
      // com os códigos se perdeu (app fechado, tempo limite). Eles não voltam;
      // a saída é gerar outros.
      if (e instanceof ErroDeApi && e.status === 409) return setEstado({ fase: "sem-codigos" });
      // O segredo pendente sumiu do servidor: só um setup novo resolve.
      if (e instanceof ErroDeApi && e.status === 400 && e.detalhe === SETUP_EXPIRADO) {
        return setEstado({ fase: "senha", aviso: "Esse QR code expirou. Confirme sua senha para gerar outro." });
      }
      setEstado({ fase: "qr", setup, aviso: texto });
    } finally {
      emVoo.current = false;
    }
  };

  if (estado.fase === "codigos") return <CodigosBackup codigos={estado.codigos} onConcluir={aoConcluir} />;

  if (estado.fase === "sem-codigos") {
    return (
      <Banner
        tom="warning"
        titulo="Verificação em 2 etapas ativa"
        mensagem="Seu MFA foi ativado, mas não recebemos os códigos de backup. Gere novos códigos."
        acao={{ rotulo: "Gerar novos códigos", onPress: aoGerarNovos }}
      />
    );
  }

  if (estado.fase === "senha" || estado.fase === "verificando-senha") {
    const verificando = estado.fase === "verificando-senha";
    return (
      <View style={{ gap: espaco.lg }}>
        <Texto variante="secao">Ativar a verificação em 2 etapas</Texto>
        <Texto variante="corpo" tom="inkMuted">
          Confirme sua senha para começar.
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
          desativado={verificando}
          erro={estado.fase === "senha" ? estado.aviso : undefined}
        />
        <Button rotulo="Continuar" onPress={() => void verificarSenha()} desativado={!senha || verificando} carregando={verificando} />
      </View>
    );
  }

  const { setup } = estado;
  const ativando = estado.fase === "ativando";
  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Adicione no app autenticador</Texto>
      <Texto variante="corpo" tom="inkMuted">
        Escaneie o QR code com o Google Authenticator, Authy ou similar. Se fechar antes de terminar, vai precisar adicionar de novo.
      </Texto>
      {/* Fundo branco nos DOIS temas: leitor de QR precisa de contraste
          escuro sobre claro, e o SVG do servidor não pinta fundo. */}
      <View
        accessible
        accessibilityLabel="QR code para o app autenticador"
        style={{ alignSelf: "center", backgroundColor: claro.bg, padding: espaco.md, borderRadius: raio.md }}
      >
        <SvgXml xml={setup.svg} width={LADO_QR} height={LADO_QR} />
      </View>
      <View style={{ gap: espaco.xs }}>
        <Texto variante="rotulo" tom="inkMuted">
          Ou digite esta chave no app
        </Texto>
        <Texto selectable variante="corpo">
          {setup.secret}
        </Texto>
      </View>
      <Button
        rotulo="Abrir no app autenticador"
        variante="secondary"
        onPress={() => {
          setAvisoLink(null);
          Linking.openURL(setup.uri).catch(() =>
            setAvisoLink("Não achamos um app autenticador neste aparelho. Copie a chave acima e cole no app que você usa."),
          );
        }}
      />
      {avisoLink ? <Banner tom="warning" mensagem={avisoLink} /> : null}
      <Input
        ref={campoCodigo}
        rotulo="Código de 6 dígitos"
        value={codigo}
        onChangeText={(v) => {
          const valor = filtrarTotp(v);
          setCodigo(valor);
          if (completouTotp(v)) void ativar(valor, setup);
        }}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="one-time-code"
        desativado={ativando}
        erro={estado.fase === "qr" ? estado.aviso : undefined}
      />
      <Button
        rotulo="Ativar"
        onPress={() => void ativar(codigo, setup)}
        desativado={ativando || codigo.length !== TAMANHO_TOTP}
        carregando={ativando}
      />
    </View>
  );
}
