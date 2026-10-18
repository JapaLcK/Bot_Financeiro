import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

// `decisao` (rosa) pede uma resposta; `alerta` (âmbar) diz que os números ou a conexão estão com problema.
// Só apresentação: a pilha não sabe de banco nem de receita (parts/AvisosDoTopo.tsx monta os itens).
// `ponto`: o nome do aviso no ponto de troca quando o título sozinho não o distingue (três receitas têm o mesmo título).
export type AvisoDaPilha = { id: string; tom: "decisao" | "alerta"; icone: string; titulo: string; ponto?: string; corpo: ReactNode; aoFechar?: () => void };

// Um aviso por vez, a borda do próximo por baixo e pontos "k de n". Mesma regra da pilha do app (pilhaAvisos.tsx):
// a seleção é o ID, não o índice (quando o visível some, cai no primeiro, sem índice fora da lista), e nada é
// anunciado ao montar. Vazia, ela não existe: só quando o ÚLTIMO aviso sai por uma AÇÃO do usuário nela (o Sim da última receita, o X) nasce um alvo de foco
// sr-only ("Nenhum aviso.") para o foco não cair no <body>; ele some quando o foco sai dele. Troca de rota e releitura nunca criam o alvo (o App já foca o título da tela).
export function PilhaAvisos({ itens }: { itens: AvisoDaPilha[] }) {
  const [sel, setSel] = useState<string | null>(null);
  const raiz = useRef<HTMLElement>(null);
  const titulo = useRef<HTMLHeadingElement>(null);
  const vazio = useRef<HTMLParagraphElement>(null);
  const [alvo, setAlvo] = useState(false);
  const pontos = useRef<HTMLDivElement>(null);
  const vista = useRef<string | undefined>(undefined);
  // O usuário acionou um BOTÃO (clique, Enter ou Espaço) DENTRO da pilha desde a última mudança da lista: é o que separa "ele respondeu" de rota e releitura.
  const agiu = useRef(false);
  const n = itens.length;
  // Seleção morta (o aviso sumiu): volta a "o primeiro". O id antigo, se voltar (Desfazer), não ressuscita.
  if (sel !== null && !itens.some((x) => x.id === sel)) setSel(null);
  const i = Math.max(0, itens.findIndex((x) => x.id === sel));
  const atual = itens[i];
  const vid = atual?.id;
  const ids = itens.map((x) => x.id).join("|");
  useEffect(() => {
    const antes = vista.current, respondeu = agiu.current;
    vista.current = vid;
    agiu.current = false;
    if (vid) setAlvo(false);
    if (!respondeu || antes === undefined || antes === vid || ids.split("|").includes(antes)) return;
    // O visível SAIU da lista (respondido, fechado): o foco ficou no vazio, então vai para o título novo. Se o usuário foi para outro lugar, não se mexe.
    const a = document.activeElement;
    if (a && a !== document.body && !raiz.current?.contains(a)) return;
    if (vid) titulo.current?.focus({ preventScroll: true });
    else setAlvo(true);
  }, [vid, ids]);
  useEffect(() => { if (alvo) vazio.current?.focus({ preventScroll: true }); }, [alvo]);
  if (!atual) return alvo ? <p ref={vazio} tabIndex={-1} className="rec-foco sr-only" onBlur={() => setAlvo(false)}>Nenhum aviso.</p> : null;
  const proximo = itens[(i + 1) % n];
  const rotulo = n > 1 ? `Aviso ${i + 1} de ${n}: ${atual.titulo}` : atual.titulo;
  const ir = (k: number) => { const alvo = itens[Math.min(n - 1, Math.max(0, k))]; if (alvo && alvo.id !== atual.id) setSel(alvo.id); };
  // ← → trocam de aviso e levam o foco junto (o equivalente web do "adjustable" do app).
  const tecla = (e: KeyboardEvent) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const k = Math.min(n - 1, Math.max(0, i + d));
    ir(k);
    pontos.current?.querySelectorAll("button")[k]?.focus();
  };
  return (
    <section ref={raiz} className="pilha" aria-label="Avisos" data-tom={atual.tom} onClickCapture={(e) => { if ((e.target as Element).closest("button")) agiu.current = true; }}>
      {/* `key` pelo aviso: o corpo (os botões) e o X são de OUTRO aviso na troca; sem ela o mesmo nó viraria o "Sim" da próxima receita. */}
      <div key={atual.id} className="pilha-card">
        <i className={`ph ${atual.icone} pilha-icone`} aria-hidden="true" />
        <div className="pilha-corpo">
          <h2 ref={titulo} tabIndex={-1} className="pilha-titulo">{n > 1 && <span className="sr-only">{`Aviso ${i + 1} de ${n}: `}</span>}{atual.titulo}</h2>
          {atual.corpo}
        </div>
        {atual.aoFechar && <button type="button" className="pilha-x" aria-label="Fechar aviso" onClick={atual.aoFechar}><i className="ph ph-x" aria-hidden="true" /></button>}
      </div>
      {n > 1 && (
        <>
          <div className="pilha-faixa" data-tom={proximo.tom} aria-hidden="true" />
          <div ref={pontos} className="pilha-pontos" role="group" aria-label="Trocar de aviso" onKeyDown={tecla}>
            {itens.map((x, k) => (
              <button key={x.id} type="button" className="pilha-ponto" data-tom={x.tom} aria-label={`Aviso ${k + 1} de ${n}: ${x.ponto ?? x.titulo}`} aria-current={k === i ? "true" : undefined} onClick={() => ir(k)}><span /></button>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
