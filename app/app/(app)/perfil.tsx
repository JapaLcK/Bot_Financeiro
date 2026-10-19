import { useFocusEffect } from "expo-router";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Linking, View } from "react-native";

import { ErroDeApi, SessaoExpirada, conferirLinhagem } from "@/api/client";
import type { Contato } from "@/api/schemas/conta";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { linkSeguro, semSenha, telefoneBR } from "@/features/conta/contato";
import { nomeMes } from "@/features/painel/catalogo";
import { useFalha } from "@/features/seguranca/falha";
import { useVoltaDoFundo } from "@/features/seguranca/voltaDoFundo";
import { perfil } from "@/services/auth";
import { gerarLinkWhatsApp, lerContato } from "@/services/conta";
import { lerCredenciais } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

type Estado =
  | { fase: "carregando" }
  | { fase: "erro" }
  | { fase: "plano" }
  | { fase: "exclusao" }
  | { fase: "pronto"; contato: Contato; precisaSenha: boolean; emailDaConta: string | null };

/** Mês de cadastro no fuso de Brasília (o instante em UTC erraria o mês na virada). */
const desde = (instante: string | null | undefined) => {
  const d = new Date(instante ?? "");
  if (Number.isNaN(d.getTime())) return null;
  const mes = new Intl.DateTimeFormat("sv-SE", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit" }).format(d);
  return `Desde ${nomeMes(mes)}`;
};

/** Um dado = ícone + valor, sem rótulo visível (a palavra só existe para o leitor de tela). Vazio = "—". Com `lido`, a linha não tem texto: o ícone (e o trailing) já dizem tudo. */
function Linha({ icone, rotulo, valor, trailing, lido }: { icone: NomeIcone; rotulo: string; valor: string | null | undefined; trailing?: ReactNode; lido?: string }) {
  const texto = valor?.trim() || null;
  return (
    <View accessible accessibilityLabel={`${rotulo}, ${lido ?? texto ?? "Não definido"}`}>
      <ListRow icone={icone} titulo={lido ? "" : (texto ?? "—")} trailing={trailing} />
    </View>
  );
}

/**
 * Perfil (P1, só leitura): nome, e-mail, telefone, WhatsApp e "Desde …". Edição é o P2.
 * `perfil()` dá o `user_id` e `precisa_criar_senha`; `lerContato` dá o resto. Recarrega a cada
 * foco e na volta do WhatsApp (`useVoltaDoFundo`), sem voltar a "carregando".
 * PII: telefone e e-mail só vão para a tela, nunca para log ou analytics.
 */
export default function Perfil() {
  const { cores } = useTema();
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  // Sem credencial (403 do link-code) ou sem número de WhatsApp no servidor: some o Conectar.
  const [bloqueio, setBloqueio] = useState<"senha" | "sem_link" | null>(null);
  const [erroWa, setErroWa] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false); // POST do Conectar em voo: o botão fica ocupado
  const geracao = useRef(0); // só a ÚLTIMA carga escreve estado (foco, volta do WhatsApp e "Tentar de novo" se sobrepõem)
  const conectando = useRef(false); // um toque por vez: o link-code limita 15/h

  const carregar = async () => {
    const minha = ++geracao.current;
    try {
      const esperada = await lerCredenciais();
      const conta = await perfil();
      // A conta trocada ENTRE as duas leituras faz a 2ª sair com o token novo e o uid antigo: o 403 de uid alheio vira RequisicaoSuperada.
      const contato = await lerContato(conta.user_id).catch(async (e: unknown) => {
        if (!(e instanceof SessaoExpirada)) await conferirLinhagem(esperada);
        throw e;
      });
      if (minha !== geracao.current) return;
      setBloqueio(null);
      setErroWa(null);
      setEstado({ fase: "pronto", contato, precisaSenha: conta.precisa_criar_senha === true, emailDaConta: conta.email?.trim() || null });
    } catch (e) {
      if (e instanceof SessaoExpirada) return void falha(e); // sessão expirada vale de qualquer geração
      if (minha !== geracao.current) return;
      if (e instanceof ErroDeApi && e.status === 402) return setEstado({ fase: "plano" });
      if (e instanceof ErroDeApi && e.status === 403 && /exclus/i.test(e.detalhe)) return setEstado({ fase: "exclusao" });
      if (falha(e) === null) { // RequisicaoSuperada: a conta trocou no meio; some o dado da anterior e relê
        setEstado({ fase: "carregando" });
        return void carregar();
      }
      // Recarga com dados na tela mantém a tela: trocar por erro desmontaria o `EsqueciSenha` e reabriria o envio.
      setEstado((atual) => (atual.fase === "pronto" ? atual : { fase: "erro" }));
    }
  };

  useFocusEffect(useCallback(() => void carregar(), [])); // `[]` de propósito: `falha` muda a cada estado da sessão (ver seguranca.tsx)
  useVoltaDoFundo(() => void carregar());

  const conectar = async () => {
    if (conectando.current) return;
    conectando.current = true;
    setErroWa(null);
    setEnviando(true);
    try {
      const { whatsapp_link: link } = await gerarLinkWhatsApp();
      if (!link) setBloqueio("sem_link");
      else if (!linkSeguro(link)) setErroWa(FALHA_WA);
      else await Linking.openURL(link);
    } catch (e) {
      if (semSenha(e)) setBloqueio("senha");
      else if (falha(e) !== null) setErroWa(e instanceof ErroDeApi && e.status === 429 ? LIMITE_WA : FALHA_WA);
    } finally {
      conectando.current = false;
      setEnviando(false);
    }
  };

  return (
    <Screen sobCabecalho>
      <View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
        {estado.fase === "carregando" && <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />}
        {estado.fase === "plano" && <Texto>Escolha um plano para ver seu perfil.</Texto>}
        {estado.fase === "exclusao" && <Texto>Esta conta está agendada para exclusão.</Texto>}
        {estado.fase === "erro" && (
          <>
            <Texto tom="danger">Não conseguimos carregar seu perfil.</Texto>
            <Button
              rotulo="Tentar de novo"
              onPress={() => {
                setEstado({ fase: "carregando" });
                void carregar();
              }}
            />
          </>
        )}
        {estado.fase === "pronto" && <Dados estado={estado} bloqueio={bloqueio} erroWa={erroWa} enviando={enviando} conectar={() => void conectar()} />}
      </View>
    </Screen>
  );
}

const FALHA_WA = "Não conseguimos abrir o WhatsApp agora. Tente de novo.";
const LIMITE_WA = "Muitas tentativas. Tente mais tarde."; // 429: o link-code limita 15/h

function Dados({ estado, bloqueio, erroWa, enviando, conectar }: { estado: Extract<Estado, { fase: "pronto" }>; bloqueio: "senha" | "sem_link" | null; erroWa: string | null; enviando: boolean; conectar: () => void }) {
  const { contato, precisaSenha, emailDaConta } = estado;
  // Conectado = existe identidade de WhatsApp. Nunca `whatsapp_verified_at`: vem preenchido sem a identidade.
  const conectado = contato.identities.some((i) => i.provider === "whatsapp");
  const semCredencial = precisaSenha || bloqueio === "senha";
  const email = contato.email?.trim() || emailDaConta;
  const criada = desde(contato.created_at);
  return (
    <>
      <GrupoDeLinhas>
        <Linha icone="UserCircle" rotulo="Nome" valor={contato.display_name} />
        <Linha icone="Envelope" rotulo="E-mail" valor={contato.email} />
        <Linha icone="Phone" rotulo="Telefone" valor={telefoneBR(contato.phone ?? null)} />
        <Linha
          icone="WhatsappLogo"
          rotulo="WhatsApp"
          valor={null}
          lido={conectado ? "Conectado" : "Não conectado"}
          trailing={conectado ? <Icone nome="CheckCircle" tom="positive" /> : undefined}
        />
        {criada && <Linha icone="CalendarBlank" rotulo="Membro" valor={criada} />}
      </GrupoDeLinhas>
      {!conectado && erroWa && <Banner tom="danger" mensagem={erroWa} />}
      {!conectado &&
        (semCredencial ? (
          email ? (
            <EsqueciSenha emailFixo={email} />
          ) : (
            <Texto>Sua conta ainda não tem senha. Defina uma senha pelo site para conectar o WhatsApp.</Texto>
          )
        ) : bloqueio === "sem_link" ? (
          <Texto>O WhatsApp ainda não está disponível. Tente mais tarde.</Texto>
        ) : (
          <Button rotulo="Conectar" carregando={enviando} onPress={conectar} />
        ))}
    </>
  );
}
