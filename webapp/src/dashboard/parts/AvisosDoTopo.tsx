// A pilha de avisos no topo do /painel, par da `cabecalho.tsx` do app: um aviso por vez, banco ANTES das receitas.
// Montada UMA vez no App (sempre montada: trocar de tela não perde o estado, nem o X, nem um POST de receita no ar) e só não desenha
// em /piggy, na Conta e no protótipo. Banco: em todas as telas. Receitas pendentes: só em `/` e em `/previsao` (as telas que mostram a previsão).
// "Conexão com o banco pendente" fica de fora: no app ela vem do cofre do aparelho (OAuth no navegador externo), e no web não há fonte equivalente.
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CONTA, type Path } from "../router";
import { locked } from "../lib/profiles.js";
import { DEMO, ErroApi, avisoDeBancoQuery, contasQuery, perfilQuery, previsaoQuery, usePlan } from "../lib/v2";
import { FeedbackReceita, itensDeReceita, useFalhaReceita, useListaDaPagina, useReceitas } from "../widgets/ReceitasDoBanco";
import { comCardPrevisao } from "./Board";
import { CorpoAvisoBanco } from "./CorpoAvisoBanco";
import { PilhaAvisos, type AvisoDaPilha } from "./PilhaAvisos";
import { useAtualizarBanco } from "./usarAtualizarBanco";

const SEM_PILHA: Path[] = ["/piggy", ...CONTA];
// Os mesmos dois códigos do `MOTIVOS_BANCO` do app (base.tsx): o estado do banco vai neste aviso, nunca repetido nos cards.
const MOTIVOS_BANCO: readonly string[] = ["banco_desatualizado", "conexao_pausada"];
// `motivos` vem do servidor: corpo sem a lista não pode derrubar o painel inteiro.
const bancoVelho = (motivos?: readonly string[]) => !!motivos?.some((m) => MOTIVOS_BANCO.includes(m));

export function AvisosDoTopo({ path }: { path: Path }) {
  const dentro = !DEMO && !SEM_PILHA.includes(path);
  const [fechado, setFechado] = useState(false); // o X do banco vale até recarregar a página (como no app, até o processo morrer)
  const [, setFalhaEm] = useFalhaReceita();
  const r = useReceitas("pendentes");
  // Nem o alerta de falha nem o anúncio da última resposta atravessam telas. Só a rota é dependência: `r.falar` muda a cada render.
  useEffect(() => { setFalhaEm(null); r.falar(""); }, [path, setFalhaEm]);
  const bancos = useQuery({ ...avisoDeBancoQuery, enabled: dentro });
  const comAviso = bancoVelho(bancos.data?.motivos) && !fechado;
  // O estado do "Atualizar agora" mora AQUI (o anfitrião sempre montado), não no corpo do aviso, que desmonta a cada troca de ponto. `dentro`: só se anuncia com a pilha na tela.
  const banco = useAtualizarBanco({ ativo: dentro && comAviso, semMotivo: !!bancos.data && !bancoVelho(bancos.data.motivos), dentro });
  useEffect(() => { banco.calar(); }, [path]); // o anúncio do banco também não atravessa telas
  const contas = useQuery({ ...contasQuery, enabled: dentro });
  // Receitas pendentes: nas telas com card/página da previsão, a lista é a que ELE leu (`useListaDaPagina`, no horizonte que ele mostra): um segundo observador da chave
  // padrão guardaria o dado do plano antigo com o card em 60 ou 90 dias, e o card o adotaria na hora no downgrade (4xx nunca mostra dado guardado).
  // Em `/` com o card no layout é a dele; em `/previsao`, a da página. Só quando o Resumo NÃO tem o card (ex.: perfil Economizar) a pilha lê a chave padrão sozinha.
  // Nas outras telas não há receita nem GET /previsao. Só o plano com previsão a lê (o mesmo critério do bloco "hero" do Resumo, `locked`): Essencial e Free levariam 403 a cada visita e foco de aba.
  // Espera o /perfil: é ele que diz se o card vai existir (`comCardPrevisao`); sem isso a pilha leria antes e o card, ao montar, abriria um 2º GET. Perfil com erro: o Board não monta card.
  const plano = usePlan();
  const resumo = dentro && path === "/" && !locked("hero", plano);
  const perfil = useQuery({ ...perfilQuery, enabled: resumo, subscribed: resumo });
  const comCard = !!perfil.data && comCardPrevisao(perfil.data.perfil ?? "padrao", plano);
  const sozinha = resumo && !perfil.isPending && !comCard;
  const previsao = useQuery({ ...previsaoQuery(null), enabled: sozinha, subscribed: sozinha });
  const [listaDaPagina] = useListaDaPagina();
  const e4 = previsao.error instanceof ErroApi && previsao.error.status >= 400 && previsao.error.status < 500;

  const nomes = [...new Set((contas.data?.contas ?? []).filter((c) => bancoVelho(c.motivos)).map((c) => c.instituicao || c.nome || "seu banco"))];
  const itens: AvisoDaPilha[] = [
    ...(comAviso ? [{
      id: "banco", tom: "alerta" as const, icone: "ph-warning", titulo: "Dados do banco desatualizados", aoFechar: () => { banco.cancelar(); setFechado(true); },
      corpo: <CorpoAvisoBanco padrao={`Os dados de ${nomes.length ? nomes.join(", ") : "um banco conectado"} precisam ser atualizados. Alguns valores podem estar incompletos.`} acao={banco.acao} maquina={banco.maquina} atualizar={banco.atualizar} />,
    }] : []),
    ...itensDeReceita((dentro && (path === "/previsao" || (resumo && comCard)) ? listaDaPagina?.receitas_do_banco : sozinha && !e4 ? previsao.data?.receitas_do_banco : null) ?? [], r),
  ];
  // As regiões de alerta e anúncio existem enquanto há receita a responder ou algo a anunciar (um alerta/status vazio em toda tela atrapalharia o resto do painel).
  // O alerta de falha só vale na tela de onde o POST saiu: o que volta 500 depois de o usuário trocar de tela não deixa alerta onde não há receita a repetir.
  // (Na tela do POST ele fica mesmo sem a receita: 404 e lista relida vazia, limite (c) do docs/CLAUDE.md.)
  const enviadoEm = useRef(path);
  useEffect(() => { if (r.pendente) enviadoEm.current = path; }, [r.pendente]);
  const comReceita = itens.some((x) => x.id.startsWith("receita:"));
  // `r.pendente`: a região viva já existe, VAZIA, quando o POST sai; o texto entra depois, e é inserção em região montada (não nascer preenchida) que o leitor de tela fala.
  const feedback = comReceita || r.pendente || !!r.dito.t || (r.falhou && enviadoEm.current === path);
  // O anúncio de um POST que voltou depois da troca de tela entra na região já montada, mas não fica pendurado: sem receita na tela ele sai em 3 s.
  useEffect(() => {
    if (comReceita || !r.dito.t) return;
    const t = setTimeout(() => r.falar(""), 3000);
    return () => clearTimeout(t);
  }, [comReceita, r.dito.n]);
  return (
    <div id="avisos-topo" ref={r.raizRef} className="avisos-topo">
      {feedback && <FeedbackReceita r={r} />}
      {/* A região do anúncio do banco existe (vazia) enquanto o aviso está na pilha: o texto que entra depois é inserção em região montada, que o leitor de tela fala. Some com o aviso, não fica pendurada em outra tela. */}
      {dentro && comAviso && <p className="sr-only" role="status">{banco.dito.t && <span key={banco.dito.n}>{banco.dito.t}</span>}</p>}
      {dentro && <PilhaAvisos itens={itens} />}
    </div>
  );
}
