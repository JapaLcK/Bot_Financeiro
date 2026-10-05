import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";

import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { textoDaFalha } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";
import { carregarAcessoBancario, type AcessoBancario } from "@/features/openFinance/acesso";
import { useForeground } from "@/features/openFinance/useForeground";
import { useBloqueio } from "@/features/bloqueio/bloqueio";
import { lerTentativaBancaria } from "@/storage/secure";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

const MENSAGEM_ERRO_SAIR = "Não conseguimos sair. Tente de novo.";

type Estado = { fase: "carregando" } | { fase: "pronto"; nome: string; acesso: AcessoBancario["fase"]; pendente: boolean } | { fase: "erro"; mensagem: string };

/**
 * Início provisório com gate de acesso e prova bancária no servidor.
 * Placeholder autenticado da Fase 3 — o ramo "pronto" da tela provisória da
 * Fase 1 (`src/ui/inicio.ts`, removida), refeito com os componentes da Fase 2.
 * A primeira tela de produto de verdade vem depois.
 */
export default function Inicio() {
  const { cores } = useTema();
  const sessao = useSessao();
  const ativo = useForeground();
  const travado = useBloqueio().estado.fase === "travado";
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  // Erro do Sair é um estado À PARTE de `estado`: uma falha ao sair não
  // invalida o perfil já carregado, então não troca a tela para "erro" (isso
  // perderia "Olá, nome" à toa) — só soma um aviso com "Tentar de novo" por
  // cima do que já está na tela.
  const [aviso, setAviso] = useState<string | null>(null);
  const [erroSaida, setErroSaida] = useState<string | null>(null);
  // A saída espera a revogação no servidor (até o tempo limite de auth): o
  // botão fica em carregando nesse meio. No sucesso a tela desmonta e o
  // `setSaindo(false)` nem roda.
  const [saindo, setSaindo] = useState(false);

  const sair = useCallback(async () => {
    setErroSaida(null);
    setSaindo(true);
    const ok = await sessao.sair();
    if (!ok) {
      setSaindo(false);
      setErroSaida(MENSAGEM_ERRO_SAIR);
    }
  }, [sessao]);

  const carregar = useCallback(async (cancelado: () => boolean = () => false) => {
    setAviso(null);
    try {
      const a = await carregarAcessoBancario();
      const p = a.perfil;
      const tentativa = a.fase === "inicio" || a.fase === "conectar" ? await lerTentativaBancaria(p.user_id) : null;
      if (cancelado()) return;
      const nome = p.display_name?.trim() || p.email?.split("@")[0] || "por aí";
      setEstado({ fase: "pronto", nome, acesso: a.fase, pendente: !!tentativa });
    } catch (e) {
      // Sessão encerrada (revogada, senha trocada): o provider decide a
      // navegação — não há erro para mostrar aqui.
      if (e instanceof SessaoExpirada) return sessao.expirou(e.detalhe);
      if (cancelado()) return;
      // Outra conta assumiu o cofre enquanto esta tela buscava o perfil dela:
      // o resultado é de uma sessão que não é mais esta tela — ignora.
      if (e instanceof RequisicaoSuperada) return;
      // `RenovacaoIndisponivel` e qualquer outra falha: instabilidade do
      // servidor, não fim de sessão — a pessoa pode tentar de novo, ou sair.
      const mensagem = textoDaFalha(e);
      setEstado((anterior) => anterior.fase === "pronto" ? anterior : { fase: "erro", mensagem });
      setAviso(mensagem);
    }
  }, [sessao]);

  useFocusEffect(useCallback(() => {
    if (!ativo || travado) return;
    let cancelado = false;
    void carregar(() => cancelado);
    return () => { cancelado = true; };
  }, [ativo, travado]));

  return (
    <Screen>
      <View style={{ flex: 1, justifyContent: "center", gap: espaco.lg }}>
        {estado.fase === "carregando" && (
          <>
            <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />
            {/* Sem isto, um `/auth/refresh` pendurado (I-E — token vencido +
                servidor que nunca responde nem falha) prendia a tela aqui
                para sempre, sem NENHUMA saída: `sair()` limpa o cofre ANTES
                de falar com a rede e espera a revogação só até o tempo
                limite, então não depende deste `perfil()` terminar. */}
            {erroSaida ? (
              <Banner tom="danger" mensagem={erroSaida} acao={{ rotulo: "Tentar de novo", onPress: () => void sair() }} />
            ) : null}
            <Button rotulo="Sair" variante="secondary" carregando={saindo} onPress={() => void sair()} />
          </>
        )}

        {estado.fase === "pronto" && (
          <>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: espaco.md }}>
              <Texto variante="titulo" style={{ flex: 1 }}>Olá, {estado.nome}</Texto>
              <Pressable accessibilityRole="button" accessibilityLabel="Configurações da conta" onPress={() => router.push("/configuracoes")}
                style={{ width: 48, height: 48, borderRadius: 24, backgroundColor: cores.surfaceRaised, alignItems: "center", justifyContent: "center" }}>
                <Texto variante="rotulo">{estado.nome.slice(0, 2).toUpperCase()}</Texto>
              </Pressable>
            </View>
            {aviso && <Banner tom="warning" mensagem={aviso} />}
            {estado.acesso === "inicio" ? (
              <Texto tom="inkMuted">Seu primeiro acesso foi concluído. Confira seus bancos e acompanhe as conexões.</Texto>
            ) : <Texto tom="inkMuted">{estado.acesso === "cobranca-pendente"
              ? "Há uma cobrança pendente. Novas conexões estão indisponíveis enquanto a cobrança é regularizada."
              : estado.acesso === "sem-acesso" ? "Sua conta ainda não tem acesso ao app. A contratação pelo iPhone chegará em uma próxima atualização."
              : estado.acesso === "sem-open-finance" ? "Seu acesso atual não permite conectar bancos."
              : estado.acesso === "senha" ? "Crie sua senha pelo link enviado por e-mail para continuar."
              : "Conecte seu banco e conclua a primeira sincronização para entrar."}</Texto>}
            {estado.acesso === "conectar" && <Button rotulo="Conectar meu banco" onPress={() => router.push("/conectar-banco")} />}
            {estado.pendente && <Button rotulo="Retomar conexão" onPress={() => router.push("/open-finance-volta")} />}
            {estado.acesso === "inicio" && <Button rotulo="Bancos conectados" variante="secondary" onPress={() => router.push("/conexoes")} />}
            <Button rotulo="Conferir acesso novamente" variante="ghost" onPress={() => void carregar()} />
            <Button rotulo="Segurança" variante="secondary" icone="Lock" onPress={() => router.push("/seguranca")} />
            {erroSaida ? (
              <Banner tom="danger" mensagem={erroSaida} acao={{ rotulo: "Tentar de novo", onPress: () => void sair() }} />
            ) : null}
            <Button rotulo="Sair" variante="secondary" carregando={saindo} onPress={() => void sair()} />
          </>
        )}

        {estado.fase === "erro" && (
          <>
            <Texto variante="corpo" tom="danger">
              {estado.mensagem}
            </Texto>
            <Button rotulo="Tentar de novo" onPress={() => void carregar()} />
            {erroSaida ? <Banner tom="danger" mensagem={erroSaida} /> : null}
            <Button rotulo="Sair" variante="secondary" carregando={saindo} onPress={() => void sair()} />
          </>
        )}
      </View>
    </Screen>
  );
}
