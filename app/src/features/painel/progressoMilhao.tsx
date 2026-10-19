import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, View } from "react-native";
import type { z } from "zod";
import { ErroDeApi, RequisicaoSuperada, SessaoExpirada } from "@/api/client";
import { progressoMilhaoSchema } from "@/api/schemas/painel";
import { useSessao } from "@/features/auth/sessao";
import { SEM_SENHA } from "@/features/openFinance/volta";
import { lerRecurso } from "@/services/painel";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { espaco } from "@/ui/tokens";
import { MilhaoCorpo } from "./milhaoCorpo";
import { codigoDe } from "./metasEscrita";
import { useNavegarPainel } from "./navegacao";
import { TextoPainel as Texto } from "./tipografia";

type Dado = z.output<typeof progressoMilhaoSchema>;
type Falha = "convite" | "senha" | "erro";
const bloco = (children: ReactNode) => <View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>{children}</View>;

/** A tela Progresso ao milhão. 4xx nunca mostra dado guardado (some o corpo); rede, 5xx e contrato com dado: mantém e avisa. Só leitura: nada é gravado. */
export function ProgressoMilhao() {
  const { expirou } = useSessao(), { navegar } = useNavegarPainel();
  const [dado, setDado] = useState<Dado | null>(null), [falha, setFalha] = useState<Falha | null>(null), [lendo, setLendo] = useState(true);
  const ctl = useRef<AbortController | null>(null);
  const ler = useCallback(async () => {
    ctl.current?.abort(); const c = (ctl.current = new AbortController());
    setLendo(true); setFalha((f) => (f === "erro" ? null : f));
    try { const d = await lerRecurso("/api/app/progresso-milhao", progressoMilhaoSchema, c); if (ctl.current !== c) return; setDado(d); setFalha(null); }
    catch (e) {
      if (ctl.current !== c) return; // outra leitura tomou o lugar, ou a tela saiu
      if (e instanceof SessaoExpirada) return expirou(e.detalhe);
      if (e instanceof RequisicaoSuperada) return;
      if (e instanceof ErroDeApi && e.status >= 400 && e.status < 500) { const k = codigoDe(e); setDado(null); setFalha(k === "pro_required" ? "convite" : k === "password_required" ? "senha" : "erro"); return; }
      setFalha("erro");
    } finally { if (ctl.current === c) setLendo(false); }
  }, [expirou]);
  useEffect(() => { void ler(); return () => { ctl.current?.abort(); ctl.current = null; }; }, [ler]);

  const corpo = dado
    ? <>
      {falha === "erro" ? <Banner tom="warning" mensagem="Não deu para atualizar. Mostrando o que carregou antes." acao={{ rotulo: "Tentar de novo", onPress: () => void ler() }} /> : null}
      {dado.motivos.includes("sem_banco_conectado")
        ? bloco(<><Texto>Conecte um banco para ver quanto falta para o seu primeiro milhão.</Texto><Button rotulo="Conectar banco" onPress={() => navegar("/conexoes")} /></>)
        : <MilhaoCorpo d={dado} />}
    </>
    : falha === "convite" ? bloco(<Texto>O Progresso ao milhão é do Plus e do Pro.</Texto>)
    : falha === "senha" ? bloco(<Texto>{SEM_SENHA}</Texto>)
    : falha === "erro" ? bloco(<><Texto>Não deu para carregar</Texto><Button rotulo="Tentar de novo" variante="secondary" onPress={() => void ler()} /></>)
    : bloco(<ActivityIndicator accessibilityLabel="Carregando" />);
  return <Screen sobCabecalho onAtualizar={() => void ler()} atualizando={lendo && dado !== null}>{corpo}</Screen>;
}
