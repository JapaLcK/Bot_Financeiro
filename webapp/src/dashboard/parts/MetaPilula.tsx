import type { CustoMensal, Meta } from "../lib/api-v2.gen";
import { moneyText } from "../lib/format.js";
import { FRASE_RITMO, MESES_PADRAO, ehZero, fraseChegada, fraseCusto, fraseRitmo, mesAno } from "../lib/metas-textos";
import { DO_ALVO } from "./MetaCartao";

// A pílula de meta do chat, igual à do app (app/src/features/painel/metasChat.tsx): ícone, nome,
// só o % (✓ se batida, ⚠ se há aviso) e a barra, ou um bloco por mês na reserva. Nenhuma frase na
// tela: elas moram no `aria-label` (o leitor de tela as lê, e o ⚠ é explicado ali). A cor da barra e do ícone é
// a de série (`data-serie`, styles/metas.css): a reserva é sempre o rosa; as outras, uma de 4 pelo `id`, não pela posição.

// Motivos que só o ritmo emite: já são a frase do ritmo, nunca um aviso à parte. Espelha
// `MOTIVOS_RITMO` de db/metas_ritmo.py (tests/test_metas_textos_constantes.py compara).
const SO_DO_RITMO = ["historico_curto", "historico_incompleto", "sem_historico_da_fonte"];

// Código novo do servidor: uma frase só, nunca o código cru (a mesma de `FRASE_GENERICA` do app).
const GENERICA = "Há informações a conferir nesta meta.";

const frase = (c: string, dias: number | null) => {
  const f = FRASE_RITMO[c as keyof typeof FRASE_RITMO];
  return (typeof f === "function" ? f(dias) : f).texto;
};

// Motivos que só acompanham o número, sem repetir a frase do ritmo que o rótulo já diz
// (regra do `_avisos` de core/services/metas_conversa.py).
function avisos(m: Meta, ritmoDito: string | null): string[] {
  const codigos = new Set<string>([...m.motivos, ...m.ritmo.motivos].filter((c) => !SO_DO_RITMO.includes(c)));
  const conhecidos = Object.keys(FRASE_RITMO).filter((c) => codigos.has(c)).map((c) => frase(c, m.ritmo.dias_de_historico));
  const desconhecido = [...codigos].some((c) => !(c in FRASE_RITMO) && !DO_ALVO.includes(c));
  return [...conhecidos, ...(desconhecido ? [GENERICA] : [])].filter((f) => f !== ritmoDito);
}

// "18000.00" → 1800000n. Dinheiro nunca passa por float; formato fora de até 2 casas → null.
const centavos = (s: string) => { const p = /^(\d+)(?:\.(\d{1,2}))?$/.exec(s); return p ? BigInt(p[1] + (p[2] ?? "").padEnd(2, "0")) : null; };

// Blocos cheios = meses cobertos: floor((alvo - falta) × meses / alvo), em centavos, de 0 a `meses`.
// Nem o `percentual` (o servidor trunca: 4 de 6 meses viraria 3 blocos) nem o `atual` (sem quantizar)
// servem de base; `falta` e `alvo` já vêm em centavos. Meta não batida (falta ≥ 1 centavo) nunca fecha todos.
function blocosCheios(m: Meta, meses: number): number {
  const alvo = m.alvo === null ? null : centavos(m.alvo), falta = m.falta === null ? null : centavos(m.falta);
  if (alvo === null || falta === null || alvo <= 0n || falta > alvo) return 0;
  return Math.min(meses, Number(((alvo - falta) * BigInt(meses)) / alvo));
}

// O que o leitor de tela lê: as frases que saíram da tela moram aqui.
// `ritmoDito`: a frase do ritmo quando ele não tem número (a tela mostraria ela no lugar do número).
function legenda(m: Meta, custo: CustoMensal, cheios: number, meses: number, batida: boolean, ritmoDito: string | null): string {
  const reserva = m.tipo === "reserva", atual = m.atual === null ? null : moneyText(m.atual), alvo = m.alvo === null ? null : moneyText(m.alvo);
  return [
    `${m.nome}${m.percentual === null ? "" : `, ${m.percentual}%`}`,
    reserva && [/reserva de emergência/i.test(m.nome) ? "" : "reserva de emergência", atual !== null && alvo !== null ? `${cheios} de ${meses} meses` : ""].filter(Boolean).join(", "),
    atual !== null && (alvo !== null ? `${atual} de ${alvo}` : `${atual} guardado`),
    batida && "meta batida",
    m.data_alvo && (m.prazo_passou ? `o prazo era ${mesAno(m.data_alvo)}` : `prazo ${mesAno(m.data_alvo)}`),
    !batida && atual !== null && fraseChegada(m),
    ritmoDito,
    reserva && fraseCusto(custo),
    ...avisos(m, ritmoDito),
  ].filter(Boolean).join(", ");
}

export const Blocos = ({ n, cheios, ok }: { n: number; cheios: number; ok?: boolean }) => (
  <span className="blocos" aria-hidden="true">
    {Array.from({ length: n }, (_, i) => <span key={i} className={i < cheios ? (ok ? "cheio ok" : "cheio") : undefined} />)}
  </span>
);

// A vaga da reserva quando ela ainda não existe.
export const PilulaSemReserva = () => (
  <div className="pilula" role="img" aria-label={`Reserva de emergência, ${MESES_PADRAO} meses, ainda não criada. Monte no painel.`}>
    <span className="pilula-topo">
      <span className="pilula-icone vazio"><i className="ph ph-piggy-bank" aria-hidden="true" /></span>
      <span className="pilula-nome faint">Monte no painel</span>
    </span>
    <Blocos n={MESES_PADRAO} cheios={0} />
  </div>
);

export function MetaPilula({ m, custo }: { m: Meta; custo: CustoMensal }) {
  const reserva = m.tipo === "reserva", batida = m.falta !== null && ehZero(m.falta), semAtual = m.atual === null, semAlvo = reserva && m.alvo === null;
  const conferir = m.atual !== null && ehZero(m.atual) && m.motivos.some((c) => !DO_ALVO.includes(c));
  const meses = m.meses ?? MESES_PADRAO, cheios = semAlvo || semAtual ? 0 : blocosCheios(m, meses);
  const ritmoDito = semAtual || (m.ritmo.valor === null && !batida) ? fraseRitmo(m.ritmo).texto : null;
  const aviso = semAtual || semAlvo || conferir || avisos(m, ritmoDito).length > 0 || (reserva && !!fraseCusto(custo));
  return (
    <div className="pilula" data-serie={reserva ? undefined : (Math.abs(m.id) % 4) + 1} role="img" aria-label={legenda(m, custo, cheios, meses, batida, ritmoDito)}>
      <span className="pilula-topo">
        <span className="pilula-icone"><i className={reserva ? "ph ph-piggy-bank" : "ph ph-target"} aria-hidden="true" /></span>
        <span className="pilula-nome">{m.nome}</span>
        {batida ? <i className="ph ph-check-circle gain" aria-hidden="true" />
          : !semAtual && m.percentual !== null && <span className="pilula-pct num">{m.percentual}%</span>}
        {aviso && <i className="ph ph-warning-circle faint" aria-hidden="true" />}
      </span>
      {reserva ? <Blocos n={meses} cheios={cheios} ok={batida} />
        : !semAtual && m.percentual !== null && <span className={`meter${batida ? " ok" : ""}`} aria-hidden="true"><span style={{ transform: `scaleX(${m.percentual / 100})` }} /></span>}
    </div>
  );
}
