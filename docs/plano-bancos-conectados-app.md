# Plano — tela "Bancos conectados" do app

Mockup aprovado pelo dono em 2026-10-17 (4 telas: tudo em dia, pede ação, nenhum banco, escuro).
Tela atual: `app/app/(app)/conexoes.tsx` (rota `/conexoes`).
Revisado pelo Arquiteto em 2026-10-17; o que ele afirmou e eu conferi no código está marcado **[conferi]**,
o resto vem do relato dele **[Arquiteto]** e o Coder reconfere antes de usar.

**Faixa: Completo** nos três PRs (saldo, Open Finance, isolamento por usuário). O fluxo de
desconectar e reconectar não muda.

## Decisões do dono (fechadas)

1. **Total de cada banco e do topo = saldo em conta + investimentos.** Exemplo do mockup: Nubank
   R$ 5.000 + R$ 40.000 = R$ 45.000; Inter R$ 7.000 + R$ 15.000 = R$ 22.000; topo R$ 67.000.
   O rótulo do topo **não é "Disponível agora"** (nome do Início: Carteira + contas, sem investido).
2. **Logo do banco entra já neste plano.**
3. Sem folha: o card abre inline. Texto mínimo (dado em forma, não em frase).
4. Fora do v1: validade da autorização (`consent_expires_at` é coluna morta para Pluggy,
   `db/open_finance.py:571`) e fatura aberta (sinal do saldo de cartão a verificar).
5. Paridade: o par no /painel (aba Open Finance de `frontend/settings.html`) é plano próprio; a rota
   nova nasce em `api/v2`, então o painel reusa a mesma soma (uma fonte só, §0.7).

## O que o plano anterior errava

- **`db/investido.py` não agrupa por conexão** **[conferi: `por_banco` agrupa por
  `c["institution_name"] or SEM_NOME`, linhas 107-110]**. Duas conexões "Nubank" já viram uma parte.
  Pôr `conexao_id` em `ParteBanco` quebraria o agrupamento por nome de que dependem a fonte `banco`
  das metas, o "meus investimentos" do WhatsApp e a tool da IA **[Arquiteto]**.
- **O app não soma dinheiro** (precedente: `/gastos` e a Previsão "não somam no cliente"). Ler `/contas` e
  `/investido` separados dá duas fotos do banco e o topo pode não fechar com os bancos. A soma mora
  no servidor, numa transação só.
- **Logo pelo catálogo no snapshot** poria uma chamada externa no caminho mais usado: o catálogo
  frio pagina ~250 itens com timeout de 20 s **[Arquiteto]**.
- **Contagem de produtos pelo snapshot** não serve: `accounts` não tira duplicata nem cartão e
  `investments` não tem `connection_id` (`db/open_finance.py:336-376`) **[Arquiteto]**.

## PR 1 — servidor: `GET /api/v2/bancos` (sai também em `/api/app/bancos`)

Só leitura, só acrescenta. `criar_app` monta a mesma lista de routers nos dois namespaces
**[conferi: `api/v2/app.py:34-38`]**; `/api/app` exige onboarding concluído
**[conferi: `api/nativo/app.py:16-25`, 403 `open_finance_onboarding_required`]**.

**Contrato** (dinheiro como texto decimal, regra da v2):
`{total, motivos, bancos: [{conexao_id, saldo_conta, investido, total, contas, cartoes, investimentos, motivos}]}`
tudo de **uma** transação repeatable read / read only, com `contas_hoje.listar` e `investido.calcular`
no mesmo cursor.

**Regras (todas reaproveitadas, nenhuma nova):**
- `saldo_conta` = soma das contas da conexão com `no_total=true`; `null` se nenhuma entra.
  Outra moeda, conexão pausada e saldo ausente ficam fora, com o motivo (`contas_hoje.py`).
- `investido` = soma das posições da conexão; `null` se a conexão não tem linha no espelho ou toda
  posição está sem saldo (mesmo critério de `_partes`).
- `total` do banco = soma das partes não nulas; `null` se as duas são. Exclusão parcial não anula o
  número e leva o motivo (vira o selo "a conferir"), como nas metas. `total` e `motivos` do topo = só
  dos bancos LISTADOS.
- Conexão sem conta, cartão e posição no espelho NÃO entra em `bancos` (decisão (c) do Arquiteto; o app
  mostra "—" e junta por `conexao_id`, nunca por posição). Ordem: `conexao_id` crescente.
- Sem o recurso `investments` do plano: `investido: null` + `plano_sem_investimentos` (só com o freio
  `PLANS_V2_ENABLED=0`; o recurso é `essencial`).
- Carteira manual, cartão e fatura ficam fora dos valores.

**Arquivos, nesta ordem:**
1. `db/investido.py`: parâmetro opcional `por_conexao` em `calcular` (molde do `com_zero`): põe
   `(por_conexao, p["connection_id"])` na tupla do laço da linha ~107. Quem não o pede recebe saída idêntica.
2. `db/contas_hoje.py`: `"conexao_id": r["connection_id"]` no dict da conta (o `response_model` de
   `/contas` o descarta, como já faz com `provider_account_id`).
3. `db/bancos.py` (novo, curto): `ler(uid)` + consulta de `cartoes` (`distinct on`, `WHERE c.user_id=%s`).
4. `api/v2/bancos.py` (novo): modelo Pydantic; `api/v2/app.py`: acrescentar `bancos` à tupla.
5. `python scripts/gerar_tipos_api_v2.py` → `webapp/src/dashboard/lib/api-v2.gen.ts`; build do `webapp/`
   com artefato idêntico (gate do CI).
6. `tests/fixtures/app_bancos.json` (validado contra o Pydantic; o jest do app usa o mesmo), linha da rota
   em `docs/CLAUDE.md`, `tests/test_api_v2_bancos.py`, `tests/fixtures/bancos_legado.json` (saídas de
   `/investido`, `/contas`, tool e WhatsApp capturadas ANTES da mudança) e as duas linhas de
   `tests/test_rotas_senha_obrigatoria.py` (toda rota nova precisa; faltou no plano do Arquiteto).

**Prova:**
- Duas conexões "Nubank" = duas entradas com valores próprios. Negativo: trocar a chave do laço para o nome reprova.
- `null` ≠ zero: conexão viva sem espelho = `investido: null` (negativo: forçar zero reprova); pausada = `null`
  + `conexao_pausada`; USD fora com `outra_moeda`; `saldo_ausente` = `null`.
- Invariantes: Σ bancos = topo; Σ `saldo_conta` = `contas.total − carteira.saldo`; Σ `investido` =
  `calcular()["total"]`, SALVO duas exceções provadas pelo Tester e documentadas em `docs/CLAUDE.md`
  (`/investido` anula o total por `saldo_ausente` com outra conexão de zero conhecido; posição só em outra moeda).
- Isolamento: dois usuários, as conexões de B nunca aparecem para A (controle positivo: as de A aparecem).
- Plano sem `investments`; portas (`/api/app` 403 sem onboarding, `/api/v2` pela chave do painel).
- `/investido`, `/contas` e `get_investment_summary` com saída idêntica antes e depois na mesma fixture;
  `test_api_v2_rotas.py` e `test_api_v2_contrato.py` verdes. Skill `pigbank-frontend` não se aplica: registrar.
- Para o PR 3 (riscos que o Manager listou): `investido: null` com `motivos: []` é o caso normal de banco sem
  investimento (quem sinaliza são os `motivos`, não o `null`); `total` do banco pode ser só o saldo; a barra só
  fecha sobre as partes não nulas; snapshot e `/bancos` são duas leituras (id numa e não na outra); o jest
  deve LER `app_bancos.json`, não copiá-lo; o decimal pode sair em notação científica (`"1E-7"`) e o
  `decimalParaCentavos` do app o recusa: tolerância por item.
- Limite a declarar: conta com `raw` sem `balance` e coluna ≠ 0 entra no total de `/contas` mas aparece
  `null` aqui (`patrimonio.sem_saldo` × `contas_hoje.py:83`).
- Só depois do deploy: números reais de staging com a conta demo.

## PR 2 — servidor: `logo_url` no snapshot (só depois da medição abaixo)

**Origem do logo:** o `raw` da conexão é o item da Pluggy (`db/open_finance.py:789`, `Jsonb(item)`) e
`item["connector"]` já é lido ali (linha ~684). Sai um escalar derivado no SQL de
`_read_open_finance_connections` (como `execution_status`): `raw->'connector'->>'imageUrl'`, validado em
Python e exposto como `logo_url` (pode ser `null`); o `raw` continua fora da resposta. Sem chamada
externa, sem catálogo frio, e conector direto que some do `/connectors` não importa.
- Validação: `^https://`, até 500 caracteres, sem espaço nem controle; senão `null`. Conexão `mock_pluggy`
  não tem `connector` e dá `null`.
- `tests/test_of_connection_state.py`: acrescentar `logo_url` em `CHAVES_DA_CONEXAO` (linha ~1355;
  `assert` de igualdade ~1823): é mudança de contrato intencional.
- Testes: aceita https; recusa http, `javascript:`, `data:`, vazio, >500, escalar que não é texto,
  objeto, `mock_pluggy`. Negativo: sem a validação, o caso `javascript:` reprova.

**Condição do PR, MEDIDA em produção em 2026-10-18 (o staging tem 0 conexões Pluggy):** `n=8`,
`com_logo=4`, `svg=4` (todo logo medido é SVG, em `https://cdn.pluggy.ai/assets/connector-icons/…`). As 4 sem
logo são as mais antigas (ids 161 a 179; 2 ACTIVE, 2 PAUSED) e o `raw` delas NÃO tem `connector`; o sync não
reescreve o `raw`, então as antigas só ganham logo ao reconectar. O dono decidiu abrir o PR assim (o app cai na
inicial onde não há logo); o plano B abaixo fica como follow-up. O texto original da condição:
- se o `connector` embutido no item traz `imageUrl` (no repositório `imageUrl` só aparece lido do
  **catálogo**, `frontend/routes/open_finance.py:1675`);
- o formato da imagem (lembrança não conferida: SVG no CDN da Pluggy). O `Image` do React Native não
  desenha SVG; `react-native-svg` 15.15.4 já está no app **[conferi]**, então `SvgUri` não pede dependência nova.
- Medição antes de codar (staging, só leitura):
  `select count(*) n, count(*) filter (where raw->'connector'->>'imageUrl' ~ '^https://') com_logo,
  count(*) filter (where raw->'connector'->>'imageUrl' ilike '%.svg%') svg
  from open_finance_connections where provider='pluggy';` e depois hosts distintos e o `content-type` de um exemplo.
- Se `com_logo` ficar bem abaixo de `n`: plano B = consulta ao catálogo **só em cache**, sem buscar nada
  dentro do snapshot.

## PR 3 — app: a tela

**Do PR 2 (`logo_url`) — limites que a tela herda:**
- O host NÃO é fixado: o validador aceita `user:pass@`, IP, `localhost`, porta e `https:///`. Quem controla o
  `imageUrl` é a Pluggy, ou quem tem o secret do webhook `item/deleted`, não o usuário. Carregar a URL faz o
  aparelho buscá-la; a PR 3 decide se fixa o host (p.ex. `cdn.pluggy.ai`).
- O formato medido em produção é SVG (4 de 4, host `cdn.pluggy.ai`); o app deve usar `SvgUri` e cair na
  inicial/`Avatar` se falhar ou se `logo_url` for `null`. Como o host medido é um só, a PR 3 pode fixá-lo.
- `logo_url` é `null` em `mock_pluggy`, em item sem `connector` e DEPOIS de `item/deleted` (o envelope do webhook
  substitui o `raw`).
- O app instalado ignora a chave (zod sem `.strict`) e o `.catch(null)` cobre servidor velho.
- Cobertura medida: 4 de 8 conexões (as antigas não têm `connector`); ver a condição acima.

O Coder invoca a skill `pigbank-frontend`.

**Comportamento que não muda:** `remover`, `abrirAutorizacao`, `navegar` e as travas `entradaEmVoo`/
`remocaoEmVoo`. A leitura de `/bancos` roda em paralelo e é **opcional**: 403 (onboarding), 404 (servidor
antigo), 5xx, rede ou schema inválido = a tela fica como hoje (lista e ações), sem números. `/conexoes` é
alcançável por `conectar-banco` antes do onboarding **[Arquiteto: `conectar-banco.tsx:68`]**.

**Arquivos:**
1. `app/src/api/schemas/openFinance.ts`: `logo_url` opcional e tolerante (`.catch(null)`).
2. `app/src/api/schemas/painel.ts`: `bancosSchema` ao lado de `contasSchema`, reusando `dinheiro`.
3. `app/src/services/openFinance.ts`: `bancos()` por `lerRecurso("/api/app/bancos", …)`.
4. `app/app/(app)/conexoes.tsx`: dado e ações.
5. `app/src/features/openFinance/conexoes/`: `ResumoBancos` (topo, barra, vagas `of_banks_max`/`em_uso`),
   `BancoCard` (abre inline; dentro: Acompanhar sincronização quando `updating`, Reconectar, Desconectar),
   `SemBancos` (sticker `hello`), `juntar(conexoes, bancos)` por id **sem aritmética de dinheiro**, e o
   helper de "há N min" (não existe no app **[Arquiteto]**; teste com relógio congelado).
6. `Avatar.tsx`: props `decorativo` (hoje lê o nome e leria o banco duas vezes) e `fundo`; círculo branco
   por token (`claro.bg`), nunca hex literal; mantém a inicial se a imagem falha; se a medição der SVG, o ramo usa `SvgUri`.
7. Cor da barra e da inicial: ver pergunta 2 abaixo.
8. `painel.fixture.json`: acrescentar `/api/app/bancos`. O `rotear` lança "rota inesperada" para URL não
   mapeada **[conferi: `auth_apoio.ts:116-120`]**, então sem isso **todos** os testes que montam a tela quebram.
9. `Icone.tsx`: `ShieldCheck` só se o mockup usar.

**Testes que citam a tela (10 arquivos [conferi: `git grep -l conexoes -- app/__tests__`, 2026-10-17]):**
"Voltar ao Início" em `open_finance_pilha.test.tsx:73`, `open_finance_entrada_rota.test.tsx:111,155` e
`open_finance_remocao_entrada.test.tsx:128`; "Conferir de novo" em `open_finance_remocao_entrada.test.tsx:143`;
"Última sincronização" em `open_finance_desconexao_rota.test.tsx` **[Arquiteto]**. As provas das travas
`entradaEmVoo` hoje usam "Voltar ao Início": ao tirar o botão, trocar o gatilho por Acompanhar ou
Reconectar, não apagar a prova. Manter os nomes acessíveis "Desconectar <banco>" e "Reconectar <banco>".

**"Voltar ao Início" pode sair:** a tela tem cabeçalho com seta (`_layout.tsx:28`) e as sete entradas
fazem `push` **[Arquiteto]**; o Coder reconfere as sete antes de remover.

**Prova:**
- Jest dos arquivos tocados, contra a baseline (lista de nomes de falha, skill `baseline-testes`).
- **O app não soma:** fixture com topo `67000.00` e snapshot listando só o Nubank: a tela mostra 67.000.
  Negativo: somar no app mostra 45.000 e reprova.
- **Mesmo nome:** dois "Nubank" com ids diferentes; juntar por `institution_name` reprova.
- **`null`:** "—", nunca "R$ 0,00" (negativo: trocar por 0 reprova).
- **Positivo:** `/bancos` em 500 ou 404 = lista aparece e Desconectar funciona.
- Logo não é lido pelo VoiceOver; tempo relativo com relógio congelado.
- Só no simulador/build: claro e escuro nos 4 estados do mockup; o logo renderiza (PNG ou SVG) e o círculo
  branco no escuro; VoiceOver "banco, estado, total"; alvo de toque ≥ 44 pt; Início e Bancos conectados lado
  a lado com a mesma conta; desconectar e reconectar de verdade em staging (conta demo).
- O app não tem atualização pelo ar (`expo-updates` ausente do `package.json` **[conferi]**): a tela só
  chega com build novo. App instalado ignora campo novo; app novo contra servidor velho cai no caminho sem números.

## Decisões do dono, 2ª rodada (2026-10-18)

1. **Ocultar valores: a tela respeita.** `oculto` é estado local do `PainelProvider`
   (`app/src/features/painel/provider.tsx:49`) e `/conexoes` fica fora dele. O estado sobe para um
   contexto no `app/app/(app)/_layout.tsx` e a tela usa o `Money` com `oculto`. Entra no PR 3: tocar
   `provider.tsx` pede o teste do Painel inteiro na baseline, e a barra de composição também some (como
   no `contasResumo.tsx`: `{!oculto && <BarraComposicao …/>}`). Teste: esconder no Painel, abrir a tela e
   não achar nenhum "R$" de valor; negativo: sem o contexto, o valor aparece e reprova.
2. **Cor do banco: só a tabela curada do app** (`coresBanco` via `corDoBanco`, sem verde nem rosa; banco
   desconhecido em escala de cinza). Nenhum campo `cor` no servidor. O PR 2 devolve só `logo_url`.

## Fora do escopo

Validade da autorização, fatura aberta, reordenar bancos, o par no /painel.
