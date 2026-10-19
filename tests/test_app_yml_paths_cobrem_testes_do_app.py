"""`.github/workflows/app.yml` dispara o jest do app quando muda um arquivo que ele lê (#993).

`paths:` do workflow `App` só cobria `app/**`, mas os testes do app leem arquivos de fora
(fixtures compartilhadas com o pytest, fontes do webapp para conferir paridade). Mudar só
um deles não rodava o jest, e a divergência só aparecia no próximo PR que tocasse `app/`.

Esta guarda extrai de `app/__tests__/**` os literais de caminho que apontam para arquivo
existente fora de `app/` e exige que cada um case algum `paths:` (push e pull_request).

Alcance: qualquer literal entre aspas simples/duplas com `/` (`import`, `require`, `readFileSync(
resolve(__dirname, "../../x"))`), com `../` opcional na frente, que exista na raiz do repo (qualquer
1º diretório, qualquer extensão) ou que passe a existir acrescentando `.ts|.tsx|.js|.jsx|.json`
(import sem extensão, ex.: `../../../webapp/src/dashboard/lib/atualizarBanco`).

Pontos cegos (esses casos são de revisão): só vê literal inteiro entre aspas. Não vê template string
(crase), caminho em segmentos (`resolve(RAIZ, "webapp", "src", "d.js")`), `RAIZ + "/..."`, diretório
inteiro (`readdirSync`), nome com `+`, `[ ]` ou `( )`, nem import transitivo dos arquivos importados
(hoje só `atualizarBanco`, que não importa nada; os demais são lidos como texto). Literal entre aspas
em comentário conta como leitura (falso positivo inofensivo: só adiciona um gatilho). E `fnmatch`
apenas aproxima o glob do GitHub.
Um padrão de negação `!` no `paths:` (ex.: `!db/metas_ritmo.py` depois do caminho) passa batido:
a guarda só confere a presença do literal, não a semântica de exclusão do GitHub.

CONTROLE NEGATIVO: tirar qualquer um dos caminhos do `app.yml` deixa o teste vermelho com o
nome do arquivo.
"""
import re
from fnmatch import fnmatch
from pathlib import Path

import yaml  # PyYAML é transitivo de `uvicorn[standard]` (não declarado em requirements.txt)

RAIZ = Path(__file__).resolve().parent.parent
LITERAL = re.compile(r"""["'](?:\.{1,2}/)*([\w.-]+/[\w./-]+)["']""")
SUFIXOS = ("", ".ts", ".tsx", ".js", ".jsx", ".json")  # import sem extensão: o jest resolve assim

# Lidos pelo app mas DE PROPÓSITO fora de `paths:` (decisão do dono, #993): o monólito muda
# muito, e o app só o lê para conferir a string `SETUP_EXPIRADO`.
SEM_GATILHO = {"frontend/finance_bot_websocket_custom.py"}


def _lidos_pelo_app() -> set[str]:
    achados = set()
    for arq in (RAIZ / "app" / "__tests__").rglob("*"):
        if arq.is_file():
            for m in LITERAL.findall(arq.read_text(errors="ignore")):
                achados |= {m + s for s in SUFIXOS if (RAIZ / (m + s)).is_file()}
    return achados - SEM_GATILHO


def test_paths_do_app_yml_cobrem_o_que_os_testes_do_app_leem():
    lidos = _lidos_pelo_app()
    # Sem isto, regex que apodrece deixaria a guarda verde sem olhar nada.
    # Um por classe: raiz `api`, extensão `.css`, import sem extensão (apontamento do Codex no PR #1038).
    assert {"webapp/src/dashboard/lib/profiles.js", "webapp/src/dashboard/parts/Board.tsx", "api/v2/metas.py",
            "webapp/src/dashboard/styles/metas.css", "webapp/src/dashboard/lib/atualizarBanco.ts"} <= lidos

    on = yaml.safe_load((RAIZ / ".github/workflows/app.yml").read_text())
    on = on.get("on", on.get(True))  # YAML 1.1 lê a chave `on` como True
    for evento in ("push", "pull_request"):
        # Sentinelas: sem elas, tirar `app/**` ou o próprio workflow deixaria a guarda verde.
        assert {"app/**", ".github/workflows/app.yml"} <= set(on[evento]["paths"]), evento
        # ponytail: fnmatch aproxima o glob do GitHub; basta para `app/**` e caminhos exatos.
        sem_gatilho = sorted(m for m in lidos if not any(fnmatch(m, p) for p in on[evento]["paths"]))
        assert not sem_gatilho, f"lidos por app/__tests__ e fora de `{evento}.paths` do app.yml: {sem_gatilho}"
    # fonte de verdade duplicada (§0.7); conjunto: ordem e repetição não mudam o gatilho no GitHub
    push, pr = set(on["push"]["paths"]), set(on["pull_request"]["paths"])
    assert push == pr, f"só em push: {sorted(push - pr)}; só em pull_request: {sorted(pr - push)}"
