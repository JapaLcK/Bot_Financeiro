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
    # Consequência para quem trunca ANTES de sanear (é o caso dos dois
    # `message[:1000]` de `system_event_logs`, e a ordem é de propósito: saneia
    # 1000 chars em vez do log inteiro): o valor gravado chega a 3× o corte —
    # MEDIDO, `len(message) == 3000` com a entrada toda de surrogates. Cabe:
    # `message` é `TEXT` e nenhum índice a cobre (`core/admin_dashboard.py:117`
    # e os dois `CREATE INDEX` de `:137`/`:143`, que são `created_at` e
    # `(level, event_type, created_at)`).
    return s.replace("\x00", _FFFD).encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def limpa_para_pg(valor):
    """Devolve `valor` sem NUL nem surrogate solitário, em qualquer profundidade.

    `str` → string saneada. `dict`/`list` → percorridos **no lugar** (chave e
    valor) e devolvidos. Qualquer outra coisa volta como veio.

    **"No lugar" é visível para quem chamou, e há chamador vivo que reusa o
    objeto**: `adapters/whatsapp/wa_app.py:311` passa a lista `status["errors"]`
    do payload por referência para o `details`, e `:325` enfileira o MESMO
    payload — a lista chega na fila com U+FFFD onde estava o byte podre
    (MEDIDO). Aceito: a troca é veneno→U+FFFD, ninguém compara byte a byte, e
    um `deepcopy` custaria mais que o saneamento inteiro. Quem precisar do
    original intacto copia ANTES de chamar.

    ponytail: teto conhecido — **`tuple` NÃO é percorrida**, volta como veio, e
    um NUL lá dentro derruba o INSERT do mesmo jeito:
    `details={"campo": ("valor\\x00podre",)}` → 0 linhas gravadas; a mesma coisa
    em `list` → 1 linha (medido). Não há chamador vivo com tupla no `details`
    (grep), por isso está documentado e não implementado. Se aparecer um, o
    conserto é tratar `tuple` no laço devolvendo `list` — `tuple` é imutável,
    então não dá para sanear no lugar.

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
                # RESSALVA (#357): isso vale para o webhook da Pluggy, que LÊ
                # chaves ASCII fixas. NÃO vale para o `details` forense de
                # auditoria, que grava o dict INTEIRO e é lido por humano:
                # `{"cpf�": "111.222.333-44", "cpf\x00": "ATACANTE"}` grava
                # `{"cpf�": "ATACANTE"}` — o campo legítimo SOME e o valor
                # do atacante é que fica. Registrado, não consertado: alternativa
                # (sufixar chave em colisão) inventa chave que não veio de
                # ninguém, e a linha existir saneada continua melhor que sumir.
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


def recusa_veneno(modelo):
    """Recusa (em vez de sanear) NUL/surrogate solitário em campo `str` de um
    modelo Pydantic. Para usar num `model_validator(mode="after")`.

    Sanear serve para dado que o sistema só GRAVA; e-mail é IDENTIFICADOR, e
    trocar `a\\x00@x.com` por `a�@x.com` inventaria identidade — o mesmo motivo
    pelo qual o `limpa_para_pg` substitui em vez de apagar. Nenhum e-mail,
    senha, nome ou código legítimo contém os dois: navegador nenhum os produz.
    O `ValueError` vira 422 pelo caminho normal do FastAPI (#369).

    ponytail: teto conhecido — só campo `str` de PRIMEIRO nível é olhado.
    MEDIDO: `list[str]`, `dict` e submodelo com surrogate PASSAM. Não é
    alcançável hoje (os herdeiros de `_CorpoSemVeneno` só têm `str`,
    `str | None` e `bool` — este último não é `str` e sai do laço sem olhar —,
    e o `extra="allow"` do pydantic v2 entra neste mesmo laço),
    mas um herdeiro futuro com campo composto passaria veneno SEM AVISO. Se
    aparecer um, o conserto é trocar o `isinstance(valor, str)` por uma
    comparação `limpa_para_pg` sobre o valor inteiro — que já caminha
    dict/list com pilha explícita. Não implementado por não ter chamador.
    """
    for nome, valor in modelo:
        if isinstance(valor, str) and _limpa_str(valor) != valor:
            raise ValueError(f"O campo '{nome}' contém caractere inválido.")
    return modelo
# A frase que substitui a mensagem do codec. Uma só, e aqui: o texto do erro é
# o mesmo em toda rota porque a CAUSA é a mesma (byte que o Postgres não
# guarda), e duas versões dela divergiriam na primeira edição (CLAUDE.md §0.7).
_ERRO_DE_CODIFICACAO = "Tem um caractere que não consigo salvar nesse texto. Apaga e digita de novo."


def detalhe_seguro(exc: Exception) -> str:
    """`str(exc)` para erro de DOMÍNIO; frase fixa para erro de CODIFICAÇÃO.

    Existe porque **`UnicodeEncodeError` é subclasse de `ValueError`** — o MRO é
    `UnicodeEncodeError → UnicodeError → ValueError`. Todo handler escrito como
    `except ValueError as exc: raise HTTPException(400, detail=str(exc))`
    captura, sem querer, o erro que o psycopg levanta ao codificar um surrogate
    solitário, e manda a mensagem interna para o cliente. MEDIDO em
    `POST /pockets/{user_id}` com usuário autenticado comum:

        {"name":"cofre\\ud800x"}
        → 400 {"detail":"'utf-8' codec can't encode character '\\ud800'
                          in position 5: surrogates not allowed"}

    O que o cliente recebia dizia o codec, o code point e o OFFSET dentro do
    campo — nada disso é para ele, e nada disso diz o que fazer.

    **Só `UnicodeError` é trocado**, e é de propósito: o `ValueError` que a
    camada `db/` levanta É a mensagem do usuário (`EMPTY_NAME`,
    `INTEREST_RATE_INVALID`, "Valor acima do limite…"), e trocá-lo por um
    genérico apagaria a única instrução útil da tela — regressão pior que o
    vazamento. Por isso o helper DISCRIMINA em vez de generalizar.

    ponytail: teto conhecido — NUL (`\\x00`) NÃO passa por aqui. O psycopg o
    recusa com `psycopg.DataError` ("PostgreSQL text fields cannot contain NUL
    (0x00) bytes"), que não é `ValueError`, não é capturado por estes handlers e
    continua virando 500 (MEDIDO em `/auth/login` e `/auth/register`). Fechar o
    NUL é recusar na fronteira, não remendar no handler — é o que o
    `recusa_veneno` faz nos corpos de auth. Aqui só se corrige o que ESTES
    `except` alcançam.
    """
    return _ERRO_DE_CODIFICACAO if isinstance(exc, UnicodeError) else str(exc)


if __name__ == "__main__":  # pragma: no cover - autocheck
    try:
        "cofre\ud800x".encode("utf-8")
    except UnicodeError as e:
        assert detalhe_seguro(e) == _ERRO_DE_CODIFICACAO, detalhe_seguro(e)
    assert detalhe_seguro(ValueError("EMPTY_NAME")) == "EMPTY_NAME"
    print("ok")
