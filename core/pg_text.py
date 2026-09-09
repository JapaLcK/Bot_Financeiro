"""Saneamento do que o **Postgres não consegue armazenar** em `text`/`jsonb`.

Restrição de ARMAZENAMENTO, não de domínio — por isso mora aqui e não no
`utils_text.py`, que é normalização de conteúdo (nome de instituição, moeda,
acento). Duas coisas do JSON de entrada não têm representação no Postgres:

- **NUL** (`\\u0000`): `text` não aceita byte zero; `jsonb` recusa `\\u0000`
  com `unsupported Unicode escape sequence`.
- **surrogate solitário** (`\\ud800`–`\\udfff` sem par): não é UTF-8 válido, e
  o psycopg estoura ao codificar o parâmetro.

Nos dois casos o resultado hoje é **500** — e no webhook da Pluggy 500 vira
laço, porque ela reenvia em erro.

**Por que substituir e não apagar.** Apagar faria `"<uuid-real>\\u0000"` virar
`"<uuid-real>"`, que **casa com um item de verdade**: o saneamento inventaria
identidade e o webhook agiria sobre a conexão errada. `U+FFFD` não aparece em
id da Pluggy, então "não casa" é garantido, e no blob forense a alteração fica
visível em vez de silenciosa.

**Por que não recusar com 400.** A Pluggy reenvia em 4xx igual: trocaria laço
de 500 por laço de 400. E o corpo pode ser legítimo em quase tudo com um byte
podre num campo que o handler nem lê — descartar perderia o `item/error` que
manda o usuário reconectar o banco.

"Preservar o original" não é uma das opções: o Postgres não guarda nenhum dos
dois. A escolha real é gravar saneado ou não gravar nada.
"""

_FFFD = "�"


def _limpa_str(s: str) -> str:
    # ponytail: um surrogate solitário vira 3 U+FFFD (são 3 bytes em
    # `surrogatepass`). Cosmético — o que importa é não casar com id real. A
    # alternativa char-a-char é O(n) em Python puro num corpo de 100 KB.
    return s.replace("\x00", _FFFD).encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def limpa_para_pg(valor):
    """Devolve `valor` sem NUL nem surrogate solitário, em qualquer profundidade.

    `str` → string saneada. `dict`/`list` → percorridos **no lugar** (chave e
    valor) e devolvidos. Qualquer outra coisa volta como veio.

    Cada `dict`/`list` é visitado **uma vez**: estrutura cíclica termina em vez
    de rodar para sempre, e o mesmo objeto alcançável por N caminhos custa N,
    não 2^N. A saída de `json.loads` não tem nenhum dos dois casos — a garantia
    é para quem reusar isto com estrutura montada em Python.
    """
    if isinstance(valor, str):
        return _limpa_str(valor)
    # Pilha explícita, nunca recursão: o scanner em C do `json` aceita mais
    # aninhamento que o limite de frames do Python, então uma caminhada
    # recursiva transforma corpo de profundidade 1500 — que hoje responde 200 —
    # em RecursionError (500) ou em 400. Medido.
    pilha = [valor]
    # Memo por `id()`: sem ele, `a["x"] = a` roda para sempre (a pilha nunca
    # esvazia — CPU pura, num worker é uma thread perdida em definitivo) e o
    # mesmo filho referenciado duas vezes por nível é 2^n visitas. `id()`
    # reusado não morde aqui — mas NÃO porque a caminhada segure os nós: chave
    # envenenada que saneia para a de um irmão DESCARTA o contêiner dele, já
    # visitado (medido). O que protege é que nada do que ela aloca vira nó:
    # o `list(no)` das chaves nunca é empilhado nem testado contra `vistos`, e
    # nenhum contêiner DA ÁRVORE nasce depois que a caminhada começa — então
    # um `id()` morto não tem como voltar como nó.
    # Cópia ou substituição de nó aqui dentro (`no[k] = dict(filho)`,
    # `deepcopy`) INVALIDA isto: aí `vistos` tem de guardar os objetos.
    vistos = set()
    while pilha:
        no = pilha.pop()
        if id(no) in vistos:
            continue
        vistos.add(id(no))
        if isinstance(no, dict):
            for chave in list(no):  # snapshot: as chaves são reinseridas no laço
                filho = no.pop(chave)
                # ponytail: duas chaves distintas podem sanear para a mesma;
                # MEDIDO, quem sobra é sempre a ENVENENADA, nas duas ordens de
                # entrada (o pop+reinsere não é last-wins como o `json.loads`).
                # Não é explorável: o saneamento só introduz U+FFFD, e as chaves
                # que este código lê são ASCII — `{"itemId":"REAL",
                # "itemI\x00d":"FALSO"}` sai com `itemId` intacto.
                no[_limpa_str(chave) if isinstance(chave, str) else chave] = (
                    _limpa_str(filho) if isinstance(filho, str) else filho
                )
                if isinstance(filho, (dict, list)):
                    pilha.append(filho)
        elif isinstance(no, list):
            for i, filho in enumerate(no):
                if isinstance(filho, str):
                    no[i] = _limpa_str(filho)
                elif isinstance(filho, (dict, list)):
                    pilha.append(filho)
    return valor
