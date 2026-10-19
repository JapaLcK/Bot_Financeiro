// O "Atualizar agora" do aviso de banco da pilha (parts/AvisosDoTopo.tsx), par de `app/src/features/painel/usarAtualizarBanco.ts`. A máquina é o porto `lib/atualizarBanco.ts`;
// aqui moram o estado, a rodada e as leituras que classificam o botão.
//
// ONDE MORA: neste hook, chamado pelo ÚNICO anfitrião sempre montado (AvisosDoTopo). O corpo do aviso desmonta a cada troca de ponto da pilha, então estado nele se perderia;
// e no web não há 4 abas montando o cabeçalho (por isso não há estado de módulo, como no app). Sem `useMutation`: a rodada é um processo longo (POST e cadência de GETs).
// RODADA = a identidade do AbortController (`vivo`): a superada perde a identidade no instante em que outra assume, então não há número nem chave para repetir.
// Toda escrita de estado e todo barulho (anúncio, recarga) passam por `atual()`.
// `marco`: o instante a partir do qual uma leitura do snapshot vale para PARTIR uma rodada sozinha (a0). Renovado na montagem, na partida, no fim, no abort e na volta da aba:
// o cache que sobrevive à remontagem (página Bancos) e a releitura velha nunca partem nada, só uma leitura posterior a ele.
//   estado \ evento  | toque                  | X / sem motivo / troca de conta / desmonte | 401-402 | 403            | aba volta a ficar visível
//   ocioso (+falha)  | (a) enviando           | ocioso                                     | —       | —              | ocioso, relê
//   enviando         | ignorado (ref)         | aborta, ocioso                             | ocioso  | ocioso + falha | segue
//   acompanhando     | (sem botão)            | aborta, ocioso                             | ocioso  | resultado      | segue (o teto é de relógio)
//   resultado        | (sem botão: sem laço)  | ocioso                                     | —       | —              | ocioso, relê
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DEMO, ErroApi, contaQuery, meQuery } from "../lib/v2";
import { atualizarBancos, comPrazo, conexoesQuery, lerConexoes, limiteQuery, uidValido } from "../conta/api";
import { PRAZO_LEITURA_MS, PRAZO_POST_MS, acompanharLista, atualizar, classificar, OCIOSO, type AcaoBanco, type Deps, type EstadoAtualizar } from "../lib/atualizarBanco";

// Rede, tempo limite, 5xx e 429 só seguem a cadência; o resto (4xx, contrato) encerra a rodada. Rede é `ErroApi(0)` e o corte é `AbortError` (`chamar`);
// qualquer outra exceção é o decodificador recusando a forma da resposta (o `ContratoInvalido` do app), que não é transitória.
const transitoria = (e: unknown) => e instanceof ErroApi ? e.status === 0 || e.status >= 500 || e.status === 429 : (e as Error | null)?.name === "AbortError";
// 401 e 402 já foram para o portão (`falhou`): a rodada para em silêncio.
const silencioso = (e: unknown) => e instanceof ErroApi && (e.status === 401 || e.status === 402);

export function useAtualizarBanco({ ativo, semMotivo, dentro }: { ativo: boolean; semMotivo: boolean; dentro: boolean }) {
  const qc = useQueryClient();
  const [estado, setEstado] = useState<{ uid: number; maquina: EstadoAtualizar }>({ uid: 0, maquina: OCIOSO });
  const [dito, setDito] = useState({ t: "", n: 0 });
  const vivo = useRef<AbortController | null>(null);
  const marco = useRef(Date.now());
  const dentroRef = useRef(dentro);
  dentroRef.current = dentro;

  const ligado = !DEMO && ativo;
  const conta = useQuery({ ...contaQuery, refetchOnWindowFocus: true, enabled: ligado });
  const id = conta.data?.user_id;
  const uid = uidValido(id) ? id : undefined;
  const conexoes = useQuery({ ...conexoesQuery(uid), enabled: ligado && uid !== undefined });
  // O limite do plano só se pergunta com candidato a reconectar (como o app); sem resposta, não oferece.
  const candidato = conexoes.isSuccess && classificar(conexoes.data, true).tipo === "reconectar";
  const limite = useQuery({ ...limiteQuery(uid), enabled: ligado && uid !== undefined && candidato });
  const pode = limite.isSuccess && limite.data.of_banks_max !== 0;
  // `null` = ainda lendo (ou a leitura falhou, inclusive o 403 de conta trocada): fica o botão "Ver bancos conectados" do PR 1.
  const acao: AcaoBanco | null = !conexoes.isSuccess || (candidato && limite.isPending) ? null : classificar(conexoes.data, pode);
  const vista = uid !== undefined && estado.uid === uid ? estado.maquina : OCIOSO;

  const calar = () => setDito((x) => (x.t ? { t: "", n: x.n + 1 } : x));
  const abortar = () => {
    vivo.current?.abort();
    vivo.current = null;
    marco.current = Date.now();
    setEstado({ uid: 0, maquina: OCIOSO });
    calar(); // o anúncio é da rodada (e da conta) que acabou: o texto do banco da conta anterior não fica na região viva
  };
  useEffect(() => () => abortar(), [uid]); // troca de conta e desmonte (o portão desmonta o App no erro do /me): nada da rodada passa por `atual()` depois
  useEffect(() => { if (semMotivo) abortar(); }, [semMotivo]); // o servidor diz que não há mais motivo: o aviso sai, a rodada para calada
  useEffect(() => {
    const volta = () => {
      if (document.visibilityState !== "visible") return;
      marco.current = Date.now(); // o refetch do foco é a releitura que reclassifica
      setEstado((e) => (e.maquina.fase === "resultado" || (e.maquina.fase === "ocioso" && e.maquina.falha) ? { uid: e.uid, maquina: OCIOSO } : e));
    };
    document.addEventListener("visibilitychange", volta);
    return () => document.removeEventListener("visibilitychange", volta);
  }, []);

  function correr(uidDaRodada: number, entrada: (d: Deps) => Promise<void>, calada = false) {
    const ctrl = new AbortController();
    vivo.current = ctrl;
    const atual = () => vivo.current === ctrl && !ctrl.signal.aborted;
    const d: Deps = {
      agora: Date.now,
      esperar: (ms) => new Promise<void>((r) => { const t = setTimeout(r, ms); ctrl.signal.addEventListener("abort", () => { clearTimeout(t); r(); }, { once: true }); }), // acorda ao abortar: a máquina para em vez de dormir até 60 s
      atual,
      enviar: () => comPrazo((s) => atualizarBancos(uidDaRodada, s), PRAZO_POST_MS, ctrl.signal),
      ler: () => comPrazo((s) => lerConexoes(uidDaRodada, s, true), PRAZO_LEITURA_MS, ctrl.signal), // a leitura que nunca volta vira AbortError (transitória): a cadência segue e o teto fecha a rodada
      aoMudar: (maquina) => { if (atual()) setEstado({ uid: uidDaRodada, maquina }); },
      recarregar: () => { if (atual()) { marco.current = Date.now(); void qc.invalidateQueries(); } }, // o mesmo que um aviso do SSE: o aviso some porque o servidor deixa de mandar o motivo
      falhou: (e) => {
        if (!(e instanceof ErroApi)) return;
        if (e.status === 401 || e.status === 402) void qc.refetchQueries({ queryKey: meQuery.queryKey }); // o portão decide (lib/eventos.ts)
        else if (e.status === 403) void conta.refetch(); // a sessão pode ser de outra conta
      },
      transitoria,
      silencioso,
      sucesso: () => {}, // ponytail: o web não vibra e o sucesso é calado (o aviso some); anunciar o sucesso pede texto aprovado pelo dono
      aviso: () => {},
      anunciar: (t) => { if (!calada && atual() && dentroRef.current) setDito((x) => ({ t, n: x.n + 1 })); }, // observa a tela de AGORA, não a de quando a rodada partiu
    };
    marco.current = Date.now();
    void entrada(d)
      .catch(() => { if (atual()) setEstado({ uid: uidDaRodada, maquina: OCIOSO }); })
      .finally(() => { if (vivo.current === ctrl) vivo.current = null; marco.current = Date.now(); });
  }

  useEffect(() => { marco.current = Date.now(); }, [ligado]); // o aviso entrou ou saiu da tela: o que já estava no cache não parte nada
  // (a0): só parte de uma leitura FEITA depois do último marco, com a fase visível diferente de `resultado`.
  useEffect(() => {
    if (!ligado || uid === undefined || !conexoes.isSuccess || conexoes.isFetching || conexoes.dataUpdatedAt <= marco.current) return;
    if (vivo.current || vista.fase === "resultado" || acao?.tipo !== "acompanhar") return;
    correr(uid, (d) => acompanharLista(d, conexoes.data), true);
  }, [conexoes.dataUpdatedAt, conexoes.isFetching, ligado, uid, vista.fase, acao?.tipo]);

  return {
    acao, maquina: vista, dito, cancelar: abortar,
    atualizar: () => { if (uid !== undefined && !vivo.current) correr(uid, atualizar); }, // o ref, não o estado: dois cliques no mesmo tique fazem 1 POST
    calar,
  };
}
