import DateTimePicker, { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Platform, Switch, View } from "react-native";

import { ErroDeApi, SessaoExpirada } from "@/api/client";
import type { Notificacoes } from "@/api/schemas/conta";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { semSenha, telefoneBR } from "@/features/conta/contato";
import { algumResumo, corpoDaHora, hhmm, semanalNaTela, semanalTravado, textoDaEscrita } from "@/features/conta/notificacoes";
import { useFalha } from "@/features/seguranca/falha";
import { useVoltaDoFundo } from "@/features/seguranca/voltaDoFundo";
import { perfil } from "@/services/auth";
import { lerNotificacoes, salvarNotificacoes, type CorpoNotificacoes } from "@/services/conta";
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
  | { fase: "senha"; email: string | null }
  | { fase: "pronto"; uid: number; dados: Notificacoes; emailDaConta: string | null };

type CampoBool = "tip_email_enabled" | "insight_email_enabled" | "whatsapp_updates_enabled" | "daily_report_enabled" | "weekly_report_enabled" | "monthly_report_enabled";

const dataDaHora = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d;
};

/** Cabeçalho de um grupo: ícone + o destino (e-mail ou telefone), sem rótulo visível; a palavra só existe para o leitor de tela. */
function Destino({ icone, rotulo, valor, children }: { icone: NomeIcone; rotulo: string; valor: string; children?: ReactNode }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: espaco.md }}>
      <View accessible accessibilityLabel={`${rotulo}, ${valor}`} style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm, flexShrink: 1 }}>
        <Icone nome={icone} tamanho={20} tom="inkMuted" />
        <Texto tom="inkMuted" numberOfLines={1} style={{ flexShrink: 1 }}>{valor}</Texto>
      </View>
      {children}
    </View>
  );
}

/**
 * Preferências de notificação: e-mail, WhatsApp e resumos. Uma escrita (PATCH) por controle, no máximo uma em voo;
 * a resposta do PATCH é a verdade (a tela só adianta o valor tocado até ela chegar). Falhou: relê, nunca reenvia.
 * PII: e-mail e telefone só vão para a tela, nunca para log ou analytics.
 */
export default function NotificacoesTela() {
  const { cores, esquema } = useTema();
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  const [previa, setPrevia] = useState<Partial<Notificacoes>>({}); // o valor tocado, só na tela, até a resposta
  const [aviso, setAviso] = useState<string | null>(null);
  const [gravando, setGravando] = useState(false);
  const geracao = useRef(0); // só a ÚLTIMA carga escreve estado; toda escrita invalida a leitura que estava em voo
  const voando = useRef(false); // trava síncrona: dois toques no mesmo tick não passam
  const releitura = useRef(false); // leitura pedida durante a escrita (foco, volta do fundo, falha): roda quando ela termina
  const rascunho = useRef<Date | null>(null); // iOS: a hora que o usuário está girando; só vale ao fechar o seletor
  const agora = useRef(estado); // o estado ATUAL: callbacks que o nativo guarda (seletor do Android) nascem num render e confirmam em outro
  agora.current = estado;

  /** Fases que valem para leitura e escrita. `true` = a tela já mudou de fase. */
  const fase = (e: unknown, email: string | null) => {
    if (!(e instanceof ErroDeApi)) return false;
    if (e.status === 402) setEstado({ fase: "plano" });
    else if (e.status === 403 && /exclus/i.test(e.detalhe)) setEstado({ fase: "exclusao" });
    else if (semSenha(e)) setEstado({ fase: "senha", email });
    else return false;
    return true;
  };

  const carregar = async () => {
    if (voando.current) return void (releitura.current = true);
    const minha = ++geracao.current;
    let emailDaConta: string | null = null;
    try {
      const conta = await perfil();
      emailDaConta = conta.email?.trim() || null;
      const dados = await lerNotificacoes(conta.user_id);
      if (minha !== geracao.current) return;
      setEstado({ fase: "pronto", uid: conta.user_id, dados, emailDaConta });
    } catch (e) {
      if (e instanceof SessaoExpirada) return void falha(e); // sessão expirada vale de qualquer geração
      if (minha !== geracao.current || fase(e, emailDaConta)) return;
      if (falha(e) === null) return; // RequisicaoSuperada: resposta de outra conta
      // Recarga com dados na tela mantém a tela: trocar por erro desmontaria o que está sendo usado.
      setEstado((atual) => (atual.fase === "pronto" ? atual : { fase: "erro" }));
    }
  };

  useFocusEffect(useCallback(() => void carregar(), [])); // `[]` de propósito: `falha` muda a cada estado da sessão (ver perfil.tsx)
  useVoltaDoFundo(() => void carregar());

  const escrever = async (corpo: CorpoNotificacoes, adiantar: Partial<Notificacoes>) => {
    if (voando.current || agora.current.fase !== "pronto") return;
    const { uid, emailDaConta } = agora.current;
    voando.current = true;
    geracao.current++;
    setGravando(true);
    setAviso(null);
    setPrevia(adiantar);
    try {
      setEstado({ fase: "pronto", uid, dados: await salvarNotificacoes(uid, corpo), emailDaConta });
    } catch (e) {
      if (e instanceof SessaoExpirada) return void falha(e);
      if (fase(e, emailDaConta)) return;
      if (falha(e) === null) { // RequisicaoSuperada: a conta trocou no meio; some o dado da anterior e relê (o /auth/me reaprende o uid)
        setEstado({ fase: "carregando" });
        releitura.current = true;
        return;
      }
      releitura.current = true; // o que o servidor gravou (ou não) é a verdade; nada é reenviado
      setAviso(textoDaEscrita(e));
    } finally {
      voando.current = false;
      setPrevia({});
      setGravando(false);
      if (releitura.current) {
        releitura.current = false;
        void carregar();
      }
    }
  };

  const confirmarHora = (d: Date) => {
    if (agora.current.fase !== "pronto") return;
    const { dados } = agora.current; // a última RESPOSTA do servidor, nunca a prévia
    const h = d.getHours(), m = d.getMinutes();
    if (h === dados.daily_report_hour && m === dados.daily_report_minute) return;
    void escrever(corpoDaHora(h, m, dados.daily_report_enabled), { daily_report_hour: h, daily_report_minute: m });
  };

  const android = Platform.OS === "android";
  const v = estado.fase === "pronto" ? { ...estado.dados, ...previa } : null;

  /** Um Switch = um PATCH com SÓ o campo tocado, montado aqui com o valor do toque (nunca a partir do estado da tela). */
  const chave = (campo: CampoBool, rotulo: string, valor: boolean, opcoes: { travado?: boolean; selo?: string; adiantar?: (novo: boolean) => Partial<Notificacoes> } = {}) => (
    <ListRow
      titulo={rotulo}
      desativado={opcoes.travado}
      trailing={
        <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm, flexShrink: 1 }}>
          {opcoes.selo ? <Texto variante="legenda" tom="inkMuted" style={{ flexShrink: 1 }}>{opcoes.selo}</Texto> : null}
          <Switch
            accessibilityLabel={rotulo}
            value={valor}
            disabled={opcoes.travado || gravando}
            onValueChange={(novo) => { if (!opcoes.travado) void escrever({ [campo]: novo }, opcoes.adiantar?.(novo) ?? { [campo]: novo }); }}
            trackColor={{ true: cores.acao }}
          />
        </View>
      }
    />
  );

  return (
    <Screen sobCabecalho>
      <View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
        {estado.fase === "carregando" && <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />}
        {estado.fase === "plano" && <Texto>Escolha um plano para ver suas notificações.</Texto>}
        {estado.fase === "exclusao" && <Texto>Esta conta está agendada para exclusão.</Texto>}
        {estado.fase === "senha" && (estado.email ? <EsqueciSenha emailFixo={estado.email} /> : <Texto>Sua conta ainda não tem senha. Defina uma senha pelo site para ver suas notificações.</Texto>)}
        {estado.fase === "erro" && (
          <>
            <Texto tom="danger">Não conseguimos carregar suas notificações.</Texto>
            <Button
              rotulo="Tentar de novo"
              onPress={() => {
                setEstado({ fase: "carregando" });
                void carregar();
              }}
            />
          </>
        )}
        {v && (
          <>
            {aviso && <Banner tom="danger" mensagem={aviso} />}
            <Destino icone="Envelope" rotulo="E-mail" valor={v.email?.trim() || "—"} />
            <GrupoDeLinhas>
              {chave("tip_email_enabled", "Dicas de uso", v.tip_email_enabled, { travado: !v.email_notifications_available })}
              {chave("insight_email_enabled", "Insights financeiros", v.insight_email_enabled, { travado: !v.email_notifications_available })}
            </GrupoDeLinhas>
            <Destino icone="WhatsappLogo" rotulo="WhatsApp" valor={telefoneBR(v.whatsapp_destination) || "—"}>
              {!v.whatsapp_updates_available && <Button rotulo="Conectar" variante="ghost" onPress={() => router.push("/perfil")} />}
            </Destino>
            <GrupoDeLinhas>
              {chave("whatsapp_updates_enabled", "Atualizações do Piggy", v.whatsapp_updates_enabled, { travado: !v.whatsapp_updates_available })}
              {chave("daily_report_enabled", "Resumo diário", v.daily_report_enabled)}
              {chave("weekly_report_enabled", "Resumo semanal", semanalNaTela(v), {
                travado: semanalTravado(v),
                selo: v.weekly_report_available ? undefined : v.weekly_report_stored_enabled ? "Pausado no seu plano" : "Plus e Pro",
                adiantar: (novo) => ({ weekly_report_enabled: novo, weekly_report_stored_enabled: novo }), // fora do plano a tela lê o gravado
              })}
              {chave("monthly_report_enabled", "Resumo mensal", v.monthly_report_enabled)}
              <ListRow
                titulo="Horário dos resumos"
                desativado={!algumResumo(v)}
                chevron={android}
                onPress={android ? () => { if (!gravando) DateTimePickerAndroid.open({ value: dataDaHora(v.daily_report_hour, v.daily_report_minute), mode: "time", is24Hour: true, onValueChange: (_, d) => confirmarHora(d) }); } : undefined}
                trailing={
                  android ? (
                    <Texto tom="inkMuted">{hhmm(v.daily_report_hour, v.daily_report_minute)}</Texto>
                  ) : (
                    <DateTimePicker
                      value={dataDaHora(v.daily_report_hour, v.daily_report_minute)}
                      mode="time"
                      display="compact"
                      themeVariant={esquema}
                      locale="pt-BR"
                      accessibilityLabel="Horário dos resumos"
                      disabled={gravando || !algumResumo(v)}
                      onValueChange={(_, d) => void (rascunho.current = d)}
                      onDismiss={() => {
                        const d = rascunho.current;
                        rascunho.current = null;
                        if (d) confirmarHora(d);
                      }}
                    />
                  )
                }
              />
            </GrupoDeLinhas>
          </>
        )}
      </View>
    </Screen>
  );
}
