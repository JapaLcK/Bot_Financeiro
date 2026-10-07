import { useQuery } from "@tanstack/react-query";
import { moneyText } from "../lib/format.js";
import { investidoQuery } from "../lib/v2";
import { rotulo } from "./Selos";

// O assunto `investido` do chat: o total dos bancos conectados, pronto do servidor
// (db/investido.py, a mesma regra do WhatsApp e da IA). Só conteúdo de linha (sem <p>,
// lista ou botão): vai dentro do <p class="msg-text"> e de novo no role=status do
// PiggyChat. Nunca float, nunca meio número.
const ERRO = "Não consegui buscar agora. Pergunta de novo daqui a pouco.";

export function InvestidoResposta() {
  const q = useQuery(investidoQuery);
  if (q.isPending) return <>Calculando…</>;
  if (q.isError) return <>{ERRO}</>;
  const d = q.data;
  if (d.total === null) {
    return <>{d.motivos.includes("sem_banco_conectado")
      ? "Ainda não sei: conecte seu banco para eu ver seus investimentos."
      : "Ainda não sei: seu banco ainda não terminou a primeira atualização."}</>;
  }
  const total = moneyText(d.total);
  const linha = (partes: { nome: string; valor: string | null }[]) =>
    partes.map((p) => (p.valor === null ? `${p.nome}: saldo não informado` : `${p.nome} ${moneyText(p.valor)}`)).join(" · ");
  const valores = [...d.por_tipo, ...d.por_banco].map((p) => p.valor);
  if (total === null || valores.some((v) => v !== null && moneyText(v) === null)) return <>{ERRO}</>;
  const selos = d.motivos.filter((m) => m !== "sem_banco_conectado");
  return (
    <>
      Você tem <b>{total}</b> investidos nos bancos conectados.
      {d.por_tipo.length > 0 && <><br />Por tipo: {linha(d.por_tipo.map((p) => ({ nome: p.rotulo, valor: p.valor })))}.</>}
      {d.por_banco.length > 0 && <><br />Por banco: {linha(d.por_banco.map((p) => ({ nome: p.banco, valor: p.valor })))}.</>}
      {selos.flatMap((m) => [" ", <span key={m} className="selo">{rotulo(m)}</span>])}
    </>
  );
}
