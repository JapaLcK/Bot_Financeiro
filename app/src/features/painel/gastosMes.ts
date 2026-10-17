import { useEffect, useRef, useState } from "react";
import { ErroDeApi, RequisicaoSuperada } from "@/api/client";
import { gastosSchema, type Gastos } from "@/api/schemas/painel";
import { lerRecurso } from "@/services/painel";
import { mensagemRecurso, usePainel, type Resultado } from "./provider";
/**
 * `GET /api/app/gastos?mes=` sob demanda (o "Seu dia": dia, quantidade, maiores e categorias). Não é recurso do provider: ninguém paga a requisição sem abrir a folha.
 * Molde do `useLancamentos`: `p.operacao()`, abort no cleanup e reinício por `versao`/usuário. Cache por [usuário, versao, mês] nesta instância, que vive enquanto o dono do hook (`FolhaDia`) ficar montado: reabrir a folha no mesmo mês não repete a leitura; trocar de mês, atualizar ou sair do painel desmonta o dono e o cache some. `mes` null = inativa (folha fechada): não lê.
 * `recarregar` repete SÓ esta leitura. Falha passa por `p.falhou` (sessão, 402 e 403 do onboarding vão ao portão); 403 de outro motivo é "negado", como nos recursos.
 */
export function useGastosMes(mes: string | null): Resultado<Gastos> & { recarregar: () => void } {
  const p = usePainel();
  const chave = `${p.usuario.user_id}|${p.versao}|${mes}`;
  const cache = useRef(new Map<string, Gastos>()); // ponytail: cresce 1 mês por leitura e só morre com a instância; as setas não saem do mês, então na prática guarda 1; um teto (LRU) se a folha passar a cruzar meses
  const [lido, setLido] = useState<{ chave: string; resultado: Resultado<Gastos> } | null>(null);
  const [tentativa, setTentativa] = useState(0);
  useEffect(() => {
    if (!mes) return;
    const feito = cache.current.get(chave);
    if (feito) { setLido({ chave, resultado: { fase: "pronto", dado: feito } }); return; }
    const op = p.operacao();
    setLido({ chave, resultado: { fase: "carregando" } });
    lerRecurso(`/api/app/gastos?mes=${mes}`, gastosSchema, op.controlador).then(
      (dado) => { cache.current.set(chave, dado); if (op.atual()) setLido({ chave, resultado: { fase: "pronto", dado } }); }, // resposta de chave velha só grava no cache da PRÓPRIA chave: não toca no `lido` da atual (que pode já ser cache)
      (e: unknown) => {
        if (!op.atual() || e instanceof RequisicaoSuperada) return;
        p.falhou(e);
        setLido({ chave, resultado: { fase: e instanceof ErroDeApi && e.status === 403 ? "negado" : "erro", mensagem: mensagemRecurso(e) } });
      });
    return () => op.controlador.abort();
  }, [chave, tentativa]);
  // Enquanto o efeito não rodou para a chave nova, o resultado é o do mês/conta ANTERIOR: nunca se mostra.
  return { ...(lido?.chave === chave ? lido.resultado : { fase: "carregando" as const }), recarregar: () => setTentativa((n) => n + 1) };
}
