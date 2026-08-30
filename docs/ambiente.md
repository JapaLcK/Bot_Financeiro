# Limites do ambiente (não são bugs, não tente contornar)

Corpo do **§6 do `CLAUDE.md` da raiz**. Ficou aqui porque é referência: só se lê
ao rodar coisas, não a cada tarefa. O §6 continua existindo lá como ponteiro —
as citações `CLAUDE.md §6` no código e nos testes seguem válidas.

**Antes de tudo: descubra em QUAL ambiente você está.** São dois, com limites
diferentes, e confundi-los já produziu documentação errada neste próprio arquivo.

```bash
ls -d /Users/<user>/Desktop/bot/bot_wa/.venv   # existe → máquina local
python3 -c "import fastapi" 2>&1 | tail -1     # ModuleNotFoundError → não é o venv
```

### 6a. Máquina local (macOS)

- **Existe um `.venv` na raiz do repositório com TODOS os pacotes**, inclusive
  `ofxparse`, `reportlab` e `pypdf`. Nada do §6b abaixo se aplica aqui: não há
  `--ignore`, não há os 9 erros de coleta, e os 7 testes de `test_statement_import.py`
  não têm por que falhar por dependência.
- **O `python3` do PATH (Homebrew) NÃO tem os pacotes** — nem `fastapi`, nem
  `psycopg`, nem `pytest`. Rodar `python3 -m pytest` dá `ModuleNotFoundError` e isso
  **não é falha de teste**. Use o interpretador do venv, com caminho absoluto:

  ```bash
  PYTHONPATH=. /Users/<user>/Desktop/bot/bot_wa/.venv/bin/python -m pytest -q
  ```

  Vale também a partir de um worktree de `.claude/worktrees/` — o venv da raiz serve
  os dois.
- **As variáveis de ambiente continuam obrigatórias** (§3 do `CLAUDE.md`). Sem elas o import de
  `frontend/finance_bot_websocket_custom.py` chama `sys.exit(1)` na linha 277
  (`DATABASE_URL`) e o pytest morre com `INTERNALERROR` — de novo, não é falha de
  teste.
- **A produção é acessível** por HTTP a partir daqui (medido: `GET https://pigbankai.com/`
  → 200, assets idem). `HEAD` responde 405 com `Allow: GET`, e isso é o roteamento
  sendo estrito, não uma falha. Antes de afirmar que algo está no ar, busque.

### 6b. Sandbox do Claude Code na web

- **A produção não é acessível**: o proxy bloqueia `pigbankai.com` (403).
- **CDNs são bloqueados.** Chart.js e afins falham; `applyTheme` do dashboard, por
  exemplo, lança no meio por causa disso. Saiba disso ao interpretar um teste.
- **Faltam pacotes de `requirements.txt`** — pelo menos `ofxparse`, `reportlab` e
  `pypdf` (o `pip install` falha pelo proxy). A ausência do `ofxparse` não
  faz "alguns testes falharem": são **9 erros de coleta**, e o pytest **interrompe a
  suíte inteira** antes de rodar qualquer teste. Sem tratar isso você não tem sinal
  nenhum — nem verde, nem vermelho. Para obter baseline local:

  A lista é **fixa** — são estes 9, e só estes:

  ```bash
  IGNORADOS=(
    tests/test_audio_clarification.py
    tests/test_audio_multi_launch_ask_value.py
    tests/test_full_handler_smoke.py
    tests/test_handle_incoming_routing.py
    tests/test_recurring_value.py
    tests/test_split_audio_transactions.py
    tests/test_whatsapp_confirmations.py
    tests/test_whatsapp_daily_report.py
    tests/test_whatsapp_simulation.py
  )

  ESPERADO="No module named 'ofxparse'"

  # Guarda: valida CAMINHO **e** CAUSA. Pareia cada "ERROR collecting <arquivo>"
  # com a linha "E <causa>" seguinte — checar só o caminho deixa passar um
  # SyntaxError novo dentro de um dos 9 (o nome do arquivo não muda).
  ERROS=$(PYTHONPATH=. python3 -m pytest -q --collect-only 2>&1 \
    | awk '/ERROR collecting /{f=$0; sub(/.*ERROR collecting /,"",f); sub(/ _*$/,"",f); next}
           f && /^E /{sub(/^E +/,""); print f" | "$0; f=""}')

  INESPERADOS=$(echo "$ERROS" | grep -vF "$ESPERADO" \
    | cat - <(echo "$ERROS" | grep -F "$ESPERADO" | cut -d' ' -f1 \
        | grep -vxF "$(printf '%s\n' "${IGNORADOS[@]}")") | grep .)

  if [ -n "$INESPERADOS" ]; then
    echo "COLETA FORA DO ESPERADO — corrija antes de tirar baseline:"; echo "$INESPERADOS"
  else
    PYTHONPATH=. python3 -m pytest -q "${IGNORADOS[@]/#/--ignore=}"
  fi
  ```

  **Não gere essa lista com `grep ERROR | sed s/^ERROR/--ignore=/`.** Esse atalho
  ignora *qualquer* erro de coleta, inclusive um que a sua própria mudança acabou de
  introduzir: um `ImportError` ou erro de sintaxe num arquivo de teste vira mais um
  `--ignore`, a rodada seguinte passa verde e o arquivo inteiro nunca roda. Testado:
  com um arquivo de sintaxe quebrada em `tests/`, o pipeline montou **10** `--ignore`
  em vez de 9 e engoliu o arquivo quebrado sem uma linha de aviso.

  **E não basta conferir o caminho.** Um `SyntaxError` novo *dentro* de um dos 9 sai
  com o mesmo nome de arquivo, então uma guarda que só compara caminhos não vê nada e
  o arquivo inteiro deixa de rodar. Medido: com um `def quebrado(` no fim de
  `tests/test_recurring_value.py`, a guarda por caminho imprimiu nada e a guarda por
  causa acusou `tests/test_recurring_value.py | File "...", line 137`. Por isso o
  bloco acima pareia arquivo **e** causa.

  Com esses 9 fora a suíte roda em pouco mais de um minuto, e as falhas restantes
  incluem sempre os de `tests/test_statement_import.py`. **Não guarde aqui quantos
  passam:** esse número é a sua baseline de comparação, e baseline lida de documento
  é pior que baseline nenhuma — ela parece medição. Meça a sua no início do trabalho
  e compare por NOME de teste (§3 do `CLAUDE.md`). Esses 7 **não vêm todos
  do `ofxparse`** — são dois grupos, e vale saber qual é qual, porque só o primeiro
  desaparece se o `ofxparse` voltar:

  | testes | dependência que falta | onde estoura |
  |---|---|---|
  | `test_attachment_detection`, `test_import_statement_bytes_csv_idempotente`, `test_import_statement_bytes_vazio_ou_grande` | `ofxparse` (import indireto, não pego pelo `--ignore` acima) | `core/handle_incoming.py` → `core/services/ofx_service.py`; e o import tardio em `statement_import.py:583` → `ofx_import.py:8` |
  | `test_parse_pdf_sicoob_like`, `test_parse_pdf_valor_e_saldo_na_mesma_linha`, `test_parse_pdf_sufixo_c_nao_engole_palavra`, `test_parse_pdf_sem_transacoes` | `reportlab` (helper `_make_pdf` do teste) e `pypdf` (`_extract_pdf_text`, `statement_import.py:463`) | o `_make_pdf` estoura primeiro; qualquer um dos dois ausente derruba os mesmos 4 |

  Os outros dois testes de PDF do arquivo (`test_parse_pdf_nubank_extrato` e
  `test_parse_pdf_nubank_sem_secao_usa_palavra_chave`) passam mesmo sem esses pacotes:
  operam sobre texto puro, sem gerar nem ler PDF.

  **O estrago não para nos testes.** `reportlab` também é importado em produção, no
  `_render_pdf` de `frontend/finance_bot_websocket_custom.py:1193`, chamado pelo
  `build_pdf` (`:1188`) da rota de exportação. Sem o pacote, exportar PDF estoura com
  `ModuleNotFoundError` — então **este ambiente não consegue exercitar esse fluxo**.
  Mexeu na exportação de PDF? Ela não foi validada aqui; diga isso no relato em vez de
  chamar de testada. São três arquivos ao todo — este, o `statement_import.py` e o
  teste:

  ```bash
  grep -rln "reportlab\|pypdf" --include="*.py" --exclude-dir=.venv --exclude-dir=.claude .
  ```

  As exclusões não são decoração: sem elas o `grep` traz também as cópias em
  `.claude/worktrees/`, e você conta o mesmo arquivo duas vezes.

  **Não trate os 7 como um bloco.** Se você mexer no parser de PDF e um desses 4 mudar
  de mensagem — de `ImportError` para uma falha de asserção, ou vice-versa — isso é
  sinal, não baseline.

  No CI os três pacotes estão presentes (`requirements.txt:56–57,66`), então lá tudo
  isso roda normalmente — a exclusão é só do sandbox.

### 6c. Vale nos dois ambientes

- **`env(safe-area-inset-*)` vale sempre 0** no Chromium headless. Regras de área
  segura são inertes ali; só dá para verificar a aritmética substituindo valores fixos.
- **Comportamento nativo do WKWebView não é reproduzível.** `contentInset`, elástico,
  teclado: só no aparelho, depois do build.
- **Exportar PDF depende de `reportlab`** (`_render_pdf`,
  `frontend/finance_bot_websocket_custom.py`). Onde o pacote falta, esse fluxo não é
  exercitável — diga isso no relato em vez de chamar de testado.

Nunca desligue verificação de TLS nem tire o `HTTPS_PROXY` para contornar bloqueio.
