# Vincular a execução local à janela do aplicativo

O piloto apresenta seu frontend web numa janela própria no Mac, e o fechamento dessa janela encerra os executores locais. O usuário confirmou essa escolha porque quer trabalho apenas com o painel aberto e retomada explícita após interrupções; uma aba comum do navegador não oferece detecção infalível do seu fechamento.

Isso exige supervisão de processos, persistência contínua e tratamento de falhas do conteúdo e do aplicativo. Enquanto a janela estiver aberta e houver trabalho, uma proteção temporária contra suspensão por inatividade permite apagar a tela; encerrar libera essa proteção. A decisão não cancela operações já aceitas pelo GitHub e não promete execução em suspensão real do Mac.

Quando o produto migrar para servidor e equipe, o ciclo de vida da execução será reavaliado explicitamente, em vez de herdar o fechamento de uma janela individual como regra global.
