# Plano do dashboard v2

Como o protótipo `webapp/src/dashboard/` (o "dashboard-v2") substitui o dashboard de
produção (`frontend/dashboard.html` + `frontend/dashboard.js`, servido em `/app`).

Decisões tomadas pelo dono em 2026-09-25, numa sessão de perguntas (Q1–Q35). Cada item
cita a pergunta que o decidiu. Sem prazo: o critério é qualidade (Q4). **Cada PR deste
plano marca aqui o que concluiu**, na seção "Andamento".

## 1. Objetivo e como a troca chega aos usuários

- **Meta final: tudo no v2.** O `dashboard.html` e o `dashboard.js` são apagados no fim
  (Q1). No caminho, a tela ainda não migrada abre no painel antigo por link, com um aviso
  discreto de "abre no painel antigo" (Q1, Q15).
- **Rota paralela `/painel`** para o v2; `/app` continua o antigo. Links nos dois sentidos
  ("Experimentar o novo painel" / "Voltar ao painel antigo"), visíveis só para quem está
  na lista de liberados; a escolha não é salva. No corte final, `/app` passa a abrir o v2
  (Q2, Q12).
- **Chave por usuário:** `dashboard_v2_enabled(user_id, email)`, no mesmo padrão das listas
  de liberados que já existem em `core/services/plan_service.py` (`agents_beta_tester`,
  `bank_list_ui_enabled`), com a lista numa variável de ambiente e o valor chegando ao
  navegador pelo `/auth/me` (Q11).
- **O app atual (Capacitor) nunca mostra o v2.** Ele carrega o site ao vivo, então os links
  de troca ficam escondidos quando o user agent é `PigBankApp/1.0` (Q10). O app novo (Expo)
  terá telas próprias e consome a mesma API nova (Q17, Q31).
- **Quando abrir para mais gente:** decisão do dono, sem critério automático (Q16).
- **Tema:** só escuro no primeiro corte; o tema claro vem numa fase seguinte (Q9).

### O que a primeira versão precisa ter (Q3)

Resumo, Lançamentos (ver, lançar, editar, apagar), Previsão, Metas e caixinhas (com
depositar e retirar), Para onde vai, Patrimônio e o chat do Piggy com IA real e blocos (Q6).
Pix, conexão do Open Finance, MFA e notificações já moram em `settings.html` /
`precos.html` e continuam lá; o v2 só aponta para elas. O resto (orçamentos, orçamento
doméstico, cartões, categorias, agentes, afiliados, exportar, importar OFX, ajuste de
saldo, vínculo do WhatsApp) abre no antigo até ser migrado, um de cada vez.

## 2. A API nova: `/api/v2`

Escolha do dono: API nova, pensada para manutenção de longo prazo, simplicidade e
tecnologia, podendo refazer o que for preciso, com calma (Q5).

- **Quem usa:** o dashboard v2 e o app novo (Expo). O WhatsApp não chama HTTP: roda no
  mesmo servidor e usa as mesmas regras de negócio direto (Q17).
- **Organização:** pasta própria `api/v2/` com `rotas/`, `esquemas/` (Pydantic) e
  `dominio/` (regras), em arquivos pequenos por assunto. Nada entra no
  `frontend/finance_bot_websocket_custom.py`; ele só registra o roteador (Q26).
- **Regras de negócio num lugar só (Q18).** Cada regra reescrita para a API nova passa a ser
  usada também pelo WhatsApp, e a versão antiga sai no mesmo PR. Nunca duas versões da
  mesma conta. (Hoje saldo e gasto do mês já estão duplicados entre o WhatsApp e o
  dashboard; ver o comentário perto de `get_financial_data` no monólito.)
- **Segurança da migração do WhatsApp (Q30):** todo PR de regra roda os testes de conversa
  (`handle_incoming`, com estado real) e compara os números antes e depois para um conjunto
  fixo de usuários de teste. Número que muda: o PR explica (conserto) ou não entra.
- **Isolamento por construção (Q20, Q23):** nenhuma rota recebe `user_id` de fora. Uma
  dependência única (`Depends(usuario_atual)`) aceita o cookie de sessão (web, com CSRF) e
  o Bearer (app), barra plano inativo e entrega o usuário. Um teste varre todas as rotas da
  `/api/v2` e falha se alguma não tiver a dependência. Cada rota tem teste de "o usuário B
  não recebe dado do A".
- **Contrato (Q22):** request e response declarados em Pydantic (`response_model` em toda
  rota) → especificação OpenAPI → tipos TypeScript gerados para o v2 e para o app (e o zod
  do app, gerado da mesma especificação). A especificação fica disponível só em
  desenvolvimento; em produção continua desligada, como hoje.
- **Erro (Q25):** envelope único `{"erro": {"codigo": "...", "mensagem": "...",
  "campo": null}}`, com os códigos listados no contrato.
- **Tempo real (Q19, Q27–Q29):** SSE, só servidor → cliente. O aviso carrega só *o que*
  mudou (`{"mudou": ["lancamentos", "saldo"]}`), nunca o dado; a tela pede de novo à API.
  Um lançamento feito pelo WhatsApp atualiza o painel aberto. Hoje roda um processo só:
  o aviso fica dentro do processo, atrás de uma função única; com uma segunda instância,
  troca-se essa função por `LISTEN/NOTIFY` do Postgres. O `/ws` antigo sai junto com o
  dashboard antigo.
- **Processo (Q21):** todo PR que cria ou muda endpoint da `/api/v2` é **faixa Completo**,
  com o time inteiro, os testes de isolamento e o Codex.

### Dados que não existem hoje (Q14, Q35)

- **Patrimônio em 12 meses:** um job diário grava uma "foto" do patrimônio de cada usuário.
  Ele entra **na etapa 0**, antes de qualquer tela, para o histórico começar a encher o quanto
  antes; enquanto enche, o gráfico diz que se completa com o tempo. Nada de reconstruir o
  passado (mostraria número errado com cara de certo).
- **Rendimento × CDI:** calculado dos investimentos e da série do CDI que já existe
  (`db/investments.py`).
- **Reserva em meses:** derivada da caixinha de reserva e das contas fixas.
- **Perfil e layout do Resumo:** perfil no servidor (coluna com `CHECK` nos ids
  `economizar|investir|controlar|dividas|autonomo|padrao`); layout segue no navegador.

## 3. O front

- **Busca de dados:** TanStack Query (cache, recarregar ao voltar para a aba, repetição,
  atualização otimista ao lançar) (Q24). O app novo pode usar a mesma biblioteca.
- **Bundle:** artefato commitado em `frontend/` com trava de rebuild no CI, como
  `precos-app.*` e `chat-app.*` (Q7). Os caminhos de asset passam a ser absolutos.
- **Plano real:** o gate de plano vem do `/auth/me` (hoje o protótipo lê `?plano=` da URL).
- **Um PR por tela** (Q8). Tela que só consome a API é faixa Leve; desde 2026-09-25 a
  faixa Leve vai **com o time, na versão leve** (decisão do dono que substituiu o
  experimento com × sem).
- **Testes (Q32):** pytest com Postgres real (isolamento e contrato por rota); Playwright
  com respostas simuladas a partir de exemplos gerados pelo contrato; ponta a ponta com
  servidor e banco de teste só nos fluxos críticos (abrir o Resumo, lançar um gasto, o aviso
  em tempo real chegar).

## 4. Ordem

**Antes de tudo (Q34):** terminar o protótipo do chat: o PR #584 (página do chat) e o PR 3
dos blocos que expandem na conversa (estado por resposta, "Abrir no painel"). A interface
fica; só as respostas prontas saem quando a IA real entrar.

| Etapa | O que entra | Faixa |
|---|---|---|
| 0 | Esqueleto da `/api/v2` (dependência de usuário, envelope de erro, contrato + tipos gerados, SSE), `/painel` servido com a chave e os links, plano real pelo `/auth/me`, cliente TanStack Query, job da foto diária do patrimônio | Completo |
| 1 | Resumo (perfil no servidor entra aqui) | API Completo, tela Leve |
| 2 | Lançamentos: ver, lançar, editar, apagar | idem |
| 3 | Previsão | idem |
| 4 | Metas e caixinhas, com depositar e retirar | idem |
| 5 | Para onde vai | idem |
| 6 | Patrimônio (com o histórico que o job da etapa 0 já vem gravando) | idem |
| 7 | Chat com IA real e blocos: o `/ai/chat` passa a devolver "texto + blocos" a partir das tools que usou | Completo |

**Depois:** migrar as demais telas uma a uma (as "em breve" de Ferramentas e as que abrem
no antigo), tema claro, abrir para mais gente, corte final (`/app` abre o v2) e apagar o
dashboard antigo e o `/ws`.

## 5. Andamento

- [x] Protótipo: perfis do Resumo (#573, #575), faixa do Piggy (#579), navegação com o
  Piggy no meio e Ferramentas (#582).
- [x] Protótipo: página do chat (#584).
- [ ] Protótipo: blocos que expandem na conversa (em outro chat).
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
