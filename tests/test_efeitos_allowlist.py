"""A allowlist de `efeitos` tem de acompanhar quem GRAVA `efeitos`.

`delete_launch_and_rollback` (`db/accounts.py`) só apaga um lançamento quando
reconhece TODAS as chaves do jsonb `efeitos`: allowlist, não denylist, para que
chave nova de escritor novo falhe FECHADA (mantém a linha) em vez de apagar
dinheiro em silêncio.

O custo dessa escolha é envelhecer calado no outro sentido: um escritor novo
com chave não classificada vira `kept_unsafe` UNIVERSAL — o usuário
simplesmente para de conseguir apagar aquele tipo de lançamento, sem erro
nenhum no log de ninguém. Esta varredura é o que impede isso: acha as chaves de
`efeitos` com `ast` e cobra que cada uma esteja classificada, seja como
reversível (`_EFEITOS_REVERSIVEIS`) ou como deliberadamente-fora
(`_EFEITOS_SEM_REVERSAO`, os `pocket_lot_*`).

O ALCANCE é o repositório inteiro (menos `tests/`, que injeta chave falsa de
propósito), não só `db/`: `extra_efeitos=` é kwarg público de
`add_launch_and_update_balance`, exportada em `db.__all__`, então qualquer
`core/`, `frontend/` ou `adapters/` pode gravar chave nova.

CEGUEIRAS DECLARADAS — o que esta varredura NÃO pega, de propósito, por ser
caro ou impossível de decidir estaticamente:
  - chave que não é literal (`efeitos[nome_da_var] = x`, f-string, `**outro`);
  - dict montado longe do nome (`d = {...}` … `add_launch(extra_efeitos=d)`) —
    exigiria seguir o fluxo de dados entre variáveis;
  - chave vinda de fora do processo (config, banco);
  - `Json({...})` inline SEM `delta_conta` — o filtro que separa `efeitos` das
    outras colunas jsonb do repositório. Um `efeitos` assim já é recusado pela
    guarda de presença, então a cegueira não vira dinheiro apagado.
Nenhuma delas aparece hoje nos escritores reais; se um dia aparecer, o sintoma
é o do parágrafo acima (delete recusa calado), não dinheiro apagado.

Mesmo padrão do `tests/test_pending_registry.py`.

Controle negativo (medido): tirando `"tax_summary"` de `_EFEITOS_REVERSIVEIS`,
`test_todo_escritor_de_efeitos_esta_classificado` falha apontando
db/pockets.py e db/investments.py.
"""
import ast
import pathlib

from db.accounts import (
    _EFEITOS_REVERSIVEIS,
    _EFEITOS_SEM_REVERSAO,
    _EFEITOS_FORA_DO_APAGAR_TUDO,
    _EFEITOS_FORMA,
)

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
# `tests/` fica de fora: os próprios testes gravam chave inventada pra provar
# que o delete recusa. O resto do repositório entra — inclusive `core/` e
# `frontend/`, que chamam `add_launch_and_update_balance(extra_efeitos=...)`.
_IGNORADOS = {".venv", ".claude", "tests", "node_modules", "mobile", ".git"}
# Nomes que recebem (ou carregam) o dict que vai para a coluna `efeitos`.
_NOMES = {"efeitos", "extra_efeitos", "deposit_effects"}
# Wrappers de jsonb do psycopg: `Json({...})` / `Jsonb({...})` inline, sem
# passar por variável (é como `import_ofx_launches_bulk` grava).
_WRAPPERS = {"Json", "Jsonb"}


def _chaves_do_dict(no: ast.Dict) -> set[str]:
    return {k.value for k in no.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _eh_alvo(no) -> bool:
    """`efeitos` / `extra_efeitos` / `deposit_effects`, como nome ou atributo."""
    return (isinstance(no, ast.Name) and no.id in _NOMES) or (
        isinstance(no, ast.Attribute) and no.attr in _NOMES
    )


def _arquivos():
    for arquivo in sorted(_RAIZ.rglob("*.py")):
        rel = arquivo.relative_to(_RAIZ)
        if not _IGNORADOS.intersection(rel.parts):
            yield rel, arquivo


def _escritores() -> dict[str, set[str]]:
    """caminho relativo → chaves de `efeitos` gravadas nele."""
    achados: dict[str, set[str]] = {}
    for rel, arquivo in _arquivos():
        try:
            arvore = ast.parse(arquivo.read_text(), filename=str(arquivo))
        except SyntaxError:  # script py2/gerado; não é escritor de efeitos
            continue
        chaves: set[str] = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Assign):
                for alvo in no.targets:
                    # `efeitos = {...}`
                    if _eh_alvo(alvo) and isinstance(no.value, ast.Dict):
                        chaves |= _chaves_do_dict(no.value)
                    # `efeitos["chave"] = ...` (cego 2 do Tester)
                    if (isinstance(alvo, ast.Subscript) and _eh_alvo(alvo.value)
                            and isinstance(alvo.slice, ast.Constant)
                            and isinstance(alvo.slice.value, str)):
                        chaves.add(alvo.slice.value)
            elif isinstance(no, ast.Call):
                # `efeitos.update({...})` — ancorado no NOME (cego 2 do Tester).
                if (isinstance(no.func, ast.Attribute) and no.func.attr == "update"
                        and _eh_alvo(no.func.value)
                        and no.args and isinstance(no.args[0], ast.Dict)):
                    chaves |= _chaves_do_dict(no.args[0])
                # `Json({...})` inline, sem passar por variável (é como
                # `import_ofx_launches_bulk` grava). Aqui não há nome pra
                # ancorar, então o marcador é a chave `delta_conta`: repo
                # inteiro tem outras colunas jsonb (`system_event_logs.meta`),
                # e sem esse filtro elas entram como se fossem `efeitos`.
                elif (isinstance(no.func, ast.Name) and no.func.id in _WRAPPERS
                        and no.args and isinstance(no.args[0], ast.Dict)):
                    do_wrapper = _chaves_do_dict(no.args[0])
                    if "delta_conta" in do_wrapper:
                        chaves |= do_wrapper
                # `add_launch_and_update_balance(..., extra_efeitos={...})` —
                # em QUALQUER arquivo (cego 1 do Tester).
                for kw in no.keywords:
                    if kw.arg in _NOMES and isinstance(kw.value, ast.Dict):
                        chaves |= _chaves_do_dict(kw.value)
                    if kw.arg in _NOMES and isinstance(kw.value, ast.Call):
                        if (isinstance(kw.value.func, ast.Name)
                                and kw.value.func.id in _WRAPPERS
                                and kw.value.args
                                and isinstance(kw.value.args[0], ast.Dict)):
                            chaves |= _chaves_do_dict(kw.value.args[0])
        if chaves:
            achados[str(rel)] = chaves
    return achados


def test_a_varredura_acha_os_escritores_conhecidos():
    """Sem este controle, um `ast` que não casa com nada passaria verde e a
    guarda de baixo não mediria coisa nenhuma."""
    achados = _escritores()
    assert {"db/accounts.py", "db/pockets.py", "db/investments.py", "db/cards.py",
            "db/open_finance.py"} <= set(achados), sorted(achados)
    # âncoras: uma chave que só existe em cada arquivo.
    assert "ofx" in achados["db/accounts.py"], achados["db/accounts.py"]
    assert "pocket_lot_create" in achados["db/pockets.py"], achados["db/pockets.py"]
    assert "investment_lot_create" in achados["db/investments.py"], achados["db/investments.py"]
    assert "bill_id" in achados["db/cards.py"], achados["db/cards.py"]
    assert "open_finance" in achados["db/open_finance.py"], achados["db/open_finance.py"]
    # o alcance é o repositório: `db/` não pode ser o único diretório varrido,
    # senão `extra_efeitos={...}` em `core/` ou `frontend/` passa despercebido.
    assert sum(1 for rel, _ in _arquivos() if rel.parts[0] == "core") > 50, "varredura presa em db/"
    assert sum(1 for rel, _ in _arquivos() if rel.parts[0] == "frontend") > 5, "varredura presa em db/"


def test_todo_escritor_de_efeitos_esta_classificado():
    classificadas = _EFEITOS_REVERSIVEIS | _EFEITOS_SEM_REVERSAO
    nao_classificadas = {
        arquivo: sorted(chaves - classificadas)
        for arquivo, chaves in _escritores().items()
        if chaves - classificadas
    }
    assert not nao_classificadas, (
        "chave nova de `efeitos` sem classificação em db/accounts.py: o delete "
        "vai RECUSAR esses lançamentos em todas as portas (fail-closed). "
        "Classifique em `_EFEITOS_REVERSIVEIS` (e implemente a reversão) ou em "
        f"`_EFEITOS_SEM_REVERSAO` (e diga por quê): {nao_classificadas}"
    )


def test_as_duas_listas_nao_se_sobrepoem():
    """`_EFEITOS_SEM_REVERSAO` só faz sentido como o COMPLEMENTO da allowlist:
    uma chave nas duas seria reversível e não-reversível ao mesmo tempo."""
    assert not (_EFEITOS_REVERSIVEIS & _EFEITOS_SEM_REVERSAO)
    # a guarda de escopo do "apagar tudo" fala de chaves que a função SABE
    # reverter — se uma delas saísse da allowlist, a guarda 2 viraria letra morta.
    assert set(_EFEITOS_FORA_DO_APAGAR_TUDO) <= _EFEITOS_REVERSIVEIS


def test_toda_chave_de_caixinha_ou_investimento_esta_fora_do_apagar_tudo():
    """Fecha a CLASSE da guarda de escopo, não a instância.

    `investment_lot_create` e `investment_lot_withdrawals` nasceram no `79bd52f`
    e ficaram DE FORA de `_EFEITOS_FORA_DO_APAGAR_TUDO` por dois commits: o
    "apagar tudo" prometia "suas caixinhas e investimentos não são afetados" e,
    com o `tipo` reescrito para `despesa`, destruía um lote (conta 700 -> -300)
    ou reabria um resgatado (conta 1000 -> 0). Duas strings esquecidas, e nada
    reprovava.

    A comparação é com `_EFEITOS_FORMA` (as chaves que a reversão EXECUTA), não
    com `_EFEITOS_REVERSIVEIS`: as seis informativas estão na allowlist e não
    tocam em tabela nenhuma, então não pertencem à guarda de escopo — e
    `investment_meta` seria falso positivo pelo nome.

    A constante segue escrita à mão de propósito. Derivá-la com este mesmo
    `"pocket"/"invest" in k` (§0.7) é 2 linhas a menos, mas põe heurística de
    SUBSTRING no caminho do dinheiro: uma chave futura com nome fora do padrão
    sairia da guarda calada, em produção, em vez de reprovar aqui. As duas
    versões são igualmente cegas a esse nome; a diferença é onde a cegueira
    aparece — teste vermelho ou caixinha apagada."""
    tocam = {k for k in _EFEITOS_FORMA if "pocket" in k or "invest" in k}
    assert tocam == set(_EFEITOS_FORA_DO_APAGAR_TUDO), (
        "chave de caixinha/investimento fora da guarda de escopo do 'apagar "
        "tudo' (ou o contrário). O 'apagar tudo' promete não tocar em caixinha "
        f"nem investimento: {sorted(tocam ^ set(_EFEITOS_FORA_DO_APAGAR_TUDO))}"
    )
    # âncora: as duas que faltavam. Sem ela, um `_EFEITOS_FORMA` podado passaria.
    assert {"investment_lot_create", "investment_lot_withdrawals"} <= tocam
    # e as de conta corrente NÃO podem entrar — seria recusar o 'apagar tudo'
    # inteiro, que é o que a função existe para fazer.
    assert not ({"delta_conta", "bill_id", "paid_amount_added"}
                & set(_EFEITOS_FORA_DO_APAGAR_TUDO))
