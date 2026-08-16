# PigBank — como trabalhar neste repositório

> **Contexto de domínio** (tabelas, endpoints, fluxos de cadastro, e-mail, roadmap):
> `docs/CLAUDE.md`. Guia de desenvolvimento: `docs/dev_guide.md`.
>
> **Este arquivo é sobre o processo**: o que fazer antes de escrever, como validar,
> e as armadilhas deste código que já custaram caro.

---

## 1. Antes de escrever a primeira linha

**Entenda a queixa antes de diagnosticar.** Relato de usuário quase nunca é
diagnóstico. Se a descrição admite mais de uma causa, pergunte pelo estado em que
elas se separam — não escolha uma e comece a codar.

> Custou um branch inteiro: "aparecem barras pretas ao puxar a tela" tinha duas
> leituras (o preto *durante* o arrasto elástico e uma faixa *permanente* sob o
> status bar). Foi tratada a errada. A pergunta que resolvia era uma só:
> "como fica com a tela parada, sem tocar?"

**Planeje antes, e por escrito.** Para qualquer coisa além de uma correção local:
liste o que vai mudar, onde, e o que pode quebrar — antes de editar. Se o plano
tem mais de três passos ou toca em mais de um arquivo, mostre-o e confirme.

**Pergunte o que a mudança torna verdade no resto do sistema.** A pergunta certa
nunca é só "isso resolve?". Uma flag de configuração de 8 caracteres pode transferir
responsabilidade para todo o produto.

> `contentInset: "never"` no `capacitor.config.json` é uma linha. Ela desliga o
> ajuste nativo de área segura nos **quatro** lados, em **todas** as rotas do
> domínio, em todas as orientações. Virou 21 arquivos e 15 apontamentos de revisão.

---

## 2. Medir o alcance — o inventário

Mudança transversal (CSS global, config nativa, middleware, schema, helper usado em
muitos lugares) **começa por uma varredura, não por uma edição**. O inventário se faz
com `grep`, nunca de memória.

Perguntas que se respondem com busca, não com raciocínio:

- **Quem mais está nesta categoria?** Corrigiu um elemento — o que mais é da mesma
  classe? (`position: fixed`, `100vw`/`100vh`, `bottom:` fixo, `z-index` alto…)
- **Com o que isto faz par?** Elementos costumam vir em pares que precisam mudar
  juntos: botão e painel, overlay e conteúdo, escrita e leitura, migração e query.
- **Quem carrega isto × quem é alcançável?** No frontend as duas listas são
  diferentes, e é aí que mora o buraco (§5).
- **Existe versão gerada fora do template?** Sim, existe (§5).

> O padrão que se repetiu 15 vezes num único PR foi sempre o mesmo erro: tratar
> "achei um caso" como "resolvi a categoria". Corrigiu o FAB e não o painel que é
> par dele; corrigiu os lados do painel e não a altura; varreu os `.html` e esqueceu
> o HTML gerado em Python.

**Seletor parecido não é contrato igual.** Antes de aplicar uma regra a N seletores,
leia o que cada um já declara. `.modal`, `.modal.wide`, `.mfa-modal` e `.pig-modal`
parecem intercambiáveis e não são — um tem largura própria, outro não tem `overflow`.
Isso gerou dois apontamentos separados.

---

## 3. Testar e validar antes de empurrar

**Nunca empurrar sem rodar a suíte.** Ela precisa de Postgres no ar e das variáveis
de ambiente — sem isso o pytest morre com `INTERNALERROR` no import, o que **não** é
falha de teste e não deve ser lido como tal:

```bash
export DATABASE_URL="postgresql://…"     # Postgres acessível
export JWT_SECRET="…"                    # 32+ bytes
export PII_ENCRYPTION_KEY="…" PII_HASH_PEPPER="…"
export PII_AUDIT_DISABLED=1 RUN_BACKGROUND_TASKS=0

PYTHONPATH=. python3 -m pytest -q                 # tudo
PYTHONPATH=. python3 -m pytest tests/test_x.py -q # durante o desenvolvimento
```

Use `python3 -m pytest`, não `pytest` solto — o binário no PATH pode ser de outro
interpretador, sem as dependências instaladas.

O CI (`.github/workflows/tests.yml`) sobe seu próprio Postgres 16 e roda `pytest`
(bloqueante) e `audit` de CVEs (não-bloqueante) em todo push e PR. Não use o CI como
primeiro teste — ele é a confirmação, não a descoberta.

**Compare com a baseline, não com zero.** Rode a suíte **antes** de mexer e guarde o
número. Falha que já existia não é regressão sua; falha nova é. Sem a baseline não dá
para distinguir as duas, e sobra "os testes estão vermelhos" sem conclusão.

**A baseline local oscila — compare por nome de teste, não por contagem.** Duas
execuções idênticas, seguidas, deram resultados diferentes:

```
execução A:   9 failed, 984 passed
execução B:  12 failed, 981 passed
```

O núcleo estável são os **7** de `tests/test_statement_import.py`, e eles **não têm uma
causa só** — são dois grupos de dependência ausente (§6). Os demais —
`test_export_email`, `test_nlp_and_pending_flow`,
`test_security_alerts` — aparecem e somem entre execuções: há dependência de ordem ou
de estado compartilhado no banco de teste. **Consequência prática:** um número de
falhas maior que o da sua baseline não prova regressão, e um igual não prova ausência
dela. Compare a **lista de nomes**, e na dúvida rode o arquivo suspeito isolado
(`pytest tests/test_x.py -q`), onde o efeito de ordem não existe.

**Frontend também se testa.** Há Chromium com Playwright disponível
(`/opt/pw-browsers/`, use `NODE_PATH=$(npm root -g)`). Mudou layout, mediu; não
"pareceu certo". E as duas perguntas que separam medição de teatro:

1. **Quanto isso daria se a correção não fizesse nada?** Se a resposta for "o mesmo
   número", a medição não mede nada — corrija o teste antes de reportar o resultado.
2. **Que classe de bug esta verificação nunca pegaria?** Se existe uma classe cega,
   ela precisa de outro método (enumeração, revisão linha a linha), porque testar
   mais do mesmo não vai alcançá-la.

> As duas perguntas vêm de erros reais: uma medição de largura de modal que dava o
> mesmo valor com e sem a correção (a CSS do componente nem estava na página), e uma
> bateria inteira de testes de área segura que era estruturalmente cega ao erro que
> estava sendo cometido, porque `env(safe-area-inset-*)` vale 0 no navegador headless.

**Verifique que não quebrou nada em volta.** Toda regra nova de CSS global, todo
helper alterado, toda mudança de schema: confira o caminho vizinho, não só o que você
consertou. `git diff` antes do commit, lido de ponta a ponta.

---

## 4. PR, Codex e threads

**Toda mudança vai por PR.** Nunca empurre direto na `main`.

**Espere o Codex.** Ele revisa automaticamente ao abrir o PR e responde a
`@codex review` num comentário. Não merje antes do parecer dele — mesmo com o CI
verde, mesmo parecendo trivial.

**Responda cada thread, e peça revisão de novo.** O ciclo completo:

1. o Codex aponta;
2. corrija — ou explique por que o apontamento não procede, com evidência;
3. **responda na própria thread**, citando o commit da correção e o que foi medido;
4. comente `@codex review` para ele reavaliar o head novo;
5. repita até ele aprovar ("Didn't find any major issues").

Nunca deixe thread sem resposta e nunca afirme que threads estão respondidas sem
abrir e conferir. **Se você pediu revisão, é sua obrigação ir ler o parecer** — não
espere alguém perguntar.

**Merge só com autorização explícita do dono do repositório.** Aprovação do Codex e
CI verde deixam o PR *pronto*; não autorizam o merge. Avise e pergunte.

**Separe por verificabilidade, não por tamanho.** Se parte do trabalho pode ser
comprovada agora e parte só depois (build de app, deploy, acesso a produção), abra
dois PRs. A metade verificável sobe sem ficar refém da outra.

---

## 5. Armadilhas conhecidas deste repositório

### Frontend: o app é o mesmo site

O app iOS (Capacitor, `mobile/`) carrega `https://pigbankai.com` num WKWebView com
`allowNavigation` para o domínio inteiro. Consequências que já causaram bug:

- **Quem carrega o CSS ≠ quem é alcançável.** `app-mode.css`/`app-mode.js` são
  carregados por **6** páginas (`login`, `cadastro`, `home`, `dashboard`,
  `comandos-app`, `settings`). Mas qualquer rota do domínio abre no app — o "Ver
  planos" do dashboard leva ao `/precos`, que não carrega nenhum dos dois. Por isso
  existe o shim `frontend/safe-area.js`, incluído em outras **14** páginas.
  As **5** restantes (`admin-login`, `admin-dashboard`, mockups e previews) não têm
  nem um nem outro, de propósito — se alguma virar rota alcançável pelo app, precisa
  entrar no shim.
- **Existe HTML gerado em Python**, fora de qualquer template:
  `frontend/finance_bot_websocket_custom.py` devolve duas páginas standalone
  (link expirado do `/d/{code}`, descadastro). O `AppDelegate.swift` carrega o
  `/d/{code}` **direto no WebView**. Toda mudança global de frontend esquece essas
  duas — já esqueceu.
- **O modo app é `html.pb-app`**, inerte na web. A classe da página vai no `<body>`
  (`pb-page-*`) **e** no `<html>` (`pb-root-*`) — o fundo do canvas (o que o elástico
  revela) vem do `<html>`, não do `<body>`.
- **`position: fixed` não herda o padding do `body`.** Todo fixo ancorado numa borda
  precisa reservar a área segura por conta própria.
- **Paisagem está habilitada no iPhone** (`mobile/ios/App/App/Info.plist`), então os
  insets laterais contam. Não trate área segura como assunto de topo.

### Componentes fragmentados

Três overlays (`.overlay`, `.pig-modal-overlay`, `.mfa-overlay`) e quatro modais
(`.modal`, `.modal.wide`, `.mfa-modal`, `.pig-modal`) fazem o mesmo trabalho com
contratos diferentes. Qualquer regra transversal precisa ser escrita para os três/quatro
— e conferindo o que cada um já declara. Enquanto isso não for unificado, é imposto
fixo de toda mudança de layout.

### Backend

- **`db/` é um pacote**, não o `db.py` único que o `docs/CLAUDE.md` descreve — aquele
  arquivo está desatualizado nesse ponto. As rotas também estão divididas em
  `frontend/routes/`.
- **Isolamento por usuário é regra dura**: toda query com `WHERE user_id = %s`.
  Nunca vazar dado entre usuários.

---

## 6. Limites deste ambiente (não são bugs, não tente contornar)

- **A produção não é acessível.** O proxy bloqueia `pigbankai.com` (403). Não dá para
  conferir o comportamento real do site daqui.
- **CDNs são bloqueados.** Chart.js e afins falham; `applyTheme` do dashboard, por
  exemplo, lança no meio por causa disso. Saiba disso ao interpretar um teste.
- **`env(safe-area-inset-*)` vale sempre 0** no Chromium headless. Regras de área
  segura são inertes aqui; só dá para verificar a aritmética substituindo valores fixos.
- **Comportamento nativo do WKWebView não é reproduzível.** `contentInset`, elástico,
  teclado: só no aparelho, depois do build.
- **Faltam pacotes de `requirements.txt` aqui** — pelo menos `ofxparse`, `reportlab` e
  `pypdf` (o `pip install` falha pelo proxy). A ausência do `ofxparse` não
  faz "alguns testes falharem": são **9 erros de coleta**, e o pytest **interrompe a
  suíte inteira** antes de rodar qualquer teste. Sem tratar isso você não tem sinal
  nenhum — nem verde, nem vermelho. Para obter baseline local:

  ```bash
  PYTHONPATH=. python3 -m pytest -q $(python3 -m pytest -q 2>&1 \
    | grep "^ERROR tests/" | sed 's/^ERROR /--ignore=/' | tr '\n' ' ')
  ```

  Com esses 9 fora, a suíte roda em ~70s: **981–984 passam**, e as falhas restantes
  incluem sempre os **7** de `tests/test_statement_import.py`. Esses 7 **não vêm todos
  do `ofxparse`** — são dois grupos, e vale saber qual é qual, porque só o primeiro
  desaparece se o `ofxparse` voltar:

  | testes | dependência que falta | onde estoura |
  |---|---|---|
  | `test_attachment_detection`, `test_import_statement_bytes_csv_idempotente`, `test_import_statement_bytes_vazio_ou_grande` | `ofxparse` (import indireto, não pego pelo `--ignore` acima) | `core/handle_incoming.py` → `core/services/ofx_service.py`; e o import tardio em `statement_import.py:583` → `ofx_import.py:8` |
  | `test_parse_pdf_sicoob_like`, `test_parse_pdf_valor_e_saldo_na_mesma_linha`, `test_parse_pdf_sufixo_c_nao_engole_palavra`, `test_parse_pdf_sem_transacoes` | `reportlab` (helper `_make_pdf` do teste) e `pypdf` (`_extract_pdf_text`, `statement_import.py:463`) | o `_make_pdf` estoura primeiro; qualquer um dos dois ausente derruba os mesmos 4 |

  Os outros dois testes de PDF do arquivo (`test_parse_pdf_nubank_extrato` e
  `test_parse_pdf_nubank_sem_secao_usa_palavra_chave`) passam mesmo sem esses pacotes:
  operam sobre texto puro, sem gerar nem ler PDF. E nada fora de
  `tests/test_statement_import.py` usa `reportlab`/`pypdf`, por isso o estrago fica
  contido nesse arquivo.

  **Não trate os 7 como um bloco.** Se você mexer no parser de PDF e um desses 4 mudar
  de mensagem — de `ImportError` para uma falha de asserção, ou vice-versa — isso é
  sinal, não baseline.

  No CI os três pacotes estão presentes (`requirements.txt:56–57,66`), então lá tudo
  isso roda normalmente — a exclusão é só local.

Nunca desligue verificação de TLS nem tire o `HTTPS_PROXY` para contornar bloqueio.

---

## 7. Como reportar o que foi feito

- **Diga o que mediu e o que não mediu.** Se algo não pôde ser verificado, diga com
  todas as letras, e diga o que *falta* para verificar ("só no aparelho, depois do
  build"). Silêncio sobre o não-verificado lê-se como verificado.
- **Números, não adjetivos.** "O botão fica dentro do card em 844×390" vale; "parece
  ok" não vale.
- **Erro admitido em uma frase, e segue.** Sem autoflagelação e sem esconder.
- **Não afirme estado sem consultar.** Antes de dizer "o CI está verde", "as threads
  estão respondidas" ou "o Codex aprovou" — abra e confira.
