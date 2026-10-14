# PL-04 PR B — controles do Xerife e gastos esperados

## Decisões de produto

- A sensibilidade é o multiplicador da média (1 a 10) e o gasto mínimo (R$ 0,01 a R$ 1 milhão). Os padrões continuam 2,5× e R$ 50. Amostra mínima, janela e comparação por lançamento do PR A permanecem.
- Feed sempre disponível; e-mail configurável pela preferência existente do Xerife. Preserva o padrão de e-mail legado. Não há novo canal WhatsApp/push nem alteração dos lembretes simples de orçamento do Essencial.
- A configuração se aplica às próximas avaliações. Não recalcula nem reenvia alertas antigos.
- Marcar esperado continua sendo uma decisão sobre um lançamento. A lista permite desfazer essa marcação e usar seus dados para preencher uma regra, que só é salva por ação explícita.
- A regra recorrente combina categoria e descrição/favorecido exatos, ignorando maiúsculas e espaços repetidos. Tem teto de valor e data final opcional, inclusiva pela data do gasto no fuso do app. Aplica-se também ao histórico; não agenda pagamentos. Há no máximo 50 regras por usuário.
- Remover a regra devolve os gastos correspondentes à referência e às próximas avaliações. As lápides dos alertas anteriores permanecem para impedir reenvio. Uma marcação individual ou outra regra ainda pode manter um gasto esperado.

## Contrato e persistência

O card do Xerife ativo ou pausado abre **Configurar alertas e esperados**. Formulários nativos usam validação local; a validação efetiva mora no backend.

| Operação | API |
| --- | --- |
| Editar sensibilidade/canal | `PATCH /agents/{user_id}/xerife/config` |
| Listar marcações, regras e config efetiva | `GET /agents/{user_id}/xerife/esperados?limit=50&offset=0` |
| Marcar/desfazer um gasto | `PUT /agents/{user_id}/xerife/lancamentos/{launch_id}/esperado` |
| Criar regra | `POST /agents/{user_id}/xerife/regras` |
| Desfazer regra | `DELETE /agents/{user_id}/xerife/regras/{regra_id}` |

Config aceita apenas `multiplicador`, `minimo`, `limites` e `email_enabled`. A ativação do Xerife usa a mesma validação e preserva campos não enviados. Números não finitos, booleanos usados como número, strings numéricas e campos desconhecidos são recusados. Regras são geridas somente pelas rotas próprias, nunca por config crua. Config legado inválido tem recuperação por campo com os mesmos valores efetivos na tela e no detector. Entradas inválidas em regras antigas são ignoradas, sem interromper o detector.

Regras ficam em `agents.config.regras_esperado`; não há migração de tabela. Escrita de regra e supressão de eventos são atômicas. A gravação do alerta reavalia a regra atual sob lock do agente para um detector iniciado antes da configuração não recolocar um gasto esperado no feed.

## Verificação e limites

Testes relacionados: `tests/test_xerife_config_regras.py`, `tests/test_xerife_config_legada.py`, `tests/frontend/xerife_config.test.mjs`, além das regressões do PR A, detector, fila de e-mail e assets. Evidências da execução corrente e revisão independente ficam em `.time-dev/tarefas/pl04-prb.md`.

Não inclui compras em cartão, detector de pico mensal por categoria nem conciliação de duplicados manual/Open Finance (PL-06/07). E-mails já enviados não podem ser desfeitos; preferências não cancelam mensagens que já estejam em envio. A data final restringe a data do gasto: gastos históricos correspondentes permanecem esperados até remover a regra. A regra não expira retroativamente o histórico.
