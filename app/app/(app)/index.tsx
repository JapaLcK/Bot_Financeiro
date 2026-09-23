import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { textoDaFalha } from "@/features/auth/entrar";
import { useSessao } from "@/features/auth/sessao";
import { perfil } from "@/services/auth";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

const MENSAGEM_ERRO_SAIR = "Não conseguimos sair. Tente de novo.";

type Estado = { fase: "carregando" } | { fase: "pronto"; nome: string } | { fase: "erro"; mensagem: string };

/**
 * Placeholder autenticado da Fase 3 — o ramo "pronto" da tela provisória da
 * Fase 1 (`src/ui/inicio.ts`, removida), refeito com os componentes da Fase 2.
 * A primeira tela de produto de verdade vem depois.
 */
export default function Inicio() {
  const { cores } = useTema();
  const sessao = useSessao();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  // Erro do Sair é um estado À PARTE de `estado`: uma falha ao sair não
  // invalida o perfil já carregado, então não troca a tela para "erro" (isso
  // perderia "Olá, nome" à toa) — só soma um aviso com "Tentar de novo" por
  // cima do que já está na tela.
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

  const carregar = useCallback(async () => {
    setEstado({ fase: "carregando" });
    try {
      const p = await perfil();
      const nome = p.display_name?.trim() || p.email?.split("@")[0] || "por aí";
      setEstado({ fase: "pronto", nome });
    } catch (e) {
      // Sessão encerrada (revogada, senha trocada): o provider decide a
      // navegação — não há erro para mostrar aqui.
      if (e instanceof SessaoExpirada) return sessao.expirou(e.detalhe);
      // Outra conta assumiu o cofre enquanto esta tela buscava o perfil dela:
      // o resultado é de uma sessão que não é mais esta tela — ignora.
      if (e instanceof RequisicaoSuperada) return;
      // `RenovacaoIndisponivel` e qualquer outra falha: instabilidade do
      // servidor, não fim de sessão — a pessoa pode tentar de novo, ou sair.
      setEstado({ fase: "erro", mensagem: textoDaFalha(e) });
    }
  }, [sessao]);

  // Só no MONTE, de propósito: `carregar` muda de referência sempre que
  // `estado` da sessão muda (o `useMemo` de `SessaoProvider` é chaveado nele),
  // e `sessao.expirou()`/`sessao.autenticar()` mudam esse estado. Um efeito
  // dependente de `carregar` reagiria a essa mudança e chamaria `/auth/me` de
  // novo — que, com a sessão já expirada, chama `expirou()` de novo, um loop
  // sem fim. "Tentar de novo" continua chamando a versão mais recente porque
  // o botão lê `carregar` do closure do render atual, não deste efeito.
  useEffect(() => {
    void carregar();
  }, []);

  return (
    <Screen rolar={false}>
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
            <Texto variante="titulo">Olá, {estado.nome}</Texto>
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
