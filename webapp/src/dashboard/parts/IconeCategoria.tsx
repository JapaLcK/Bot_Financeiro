// O ícone Phosphor da categoria: o servidor deriva `icone` do emoji (`db/cat_icones.py`) e o emoji segue no payload, sem ser desenhado.
// `tag` quando o servidor é velho e não manda o campo, ou manda algo que não é nome de ícone (`__proto__`, `a b c`).
// ponytail: só o formato é checado; `constructor` passa na regex e vira uma classe sem glifo, o que o servidor nunca manda (nomes de `cat_icones.py`).
const NOME_DE_ICONE = /^[a-z0-9-]+$/;
export const IconeCategoria = ({ icone }: { icone?: string }) => <i className={`ph ph-${NOME_DE_ICONE.test(icone ?? "") ? icone : "tag"}`} aria-hidden="true" />;
