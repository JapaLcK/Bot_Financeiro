# ADR 0004 — O app nativo fala com o backend sem cookie

Status: aceito para implementação em 15/09/2026, decisão explícita do dono na Fase 1 do PigBank Mobile 2.0.

O aplicativo reconstruído não usa cookie jar. A sessão viaja em `Authorization: Bearer`, guardada no keychain do aparelho. O backend entrega as credenciais no corpo da resposta para quem envia `X-PigBank-Client: app`, e para esse cliente não emite `Set-Cookie` nenhum. Sem o header, toda resposta continua idêntica ao que o site recebe hoje: três cookies e nenhum token no corpo.

A escolha não é de conveniência. Com cookie jar perde-se rotação limpa de token, logout confiável e renovação fora do navegador. E há um efeito concreto na direção contrária: o `fetch` do React Native tem cookie jar ligado por padrão, então um `Set-Cookie` que o servidor mandasse seria guardado sem que o app pedisse, e a requisição seguinte passaria a carregar credencial ambiente. O CSRF então voltaria a exigir o par cookie mais cabeçalho, que o app não tem, e a segunda escrita falharia depois de a primeira ter funcionado. As duas metades da decisão — token no corpo e ausência de cookie — são a mesma decisão, e por isso moram na mesma função.

## A isenção de CSRF, e o que a sustenta

O CSRF é dispensado quando a requisição não traz **nenhum** cookie e declara corpo JSON. O que o CSRF protege é credencial **ambiente**: o navegador anexa o cookie sozinho, então uma página de terceiro dispara uma escrita autenticada sem precisar ler nada da vítima. Sem cookie de sessão não existe credencial ambiente, e o par deixa de proteger alguma coisa.

Nenhum cabeçalho de identificação participa da decisão, nem `Authorization`, nem `X-PigBank-Client`. Isso é deliberado: cabeçalho de identidade é alegação de quem chama, e uma isenção que dependesse dele seria contornável mandando a alegação. O que o atacante não controla é o cookie — se a vítima tem sessão, o navegador o envia sozinho, e o par volta a ser exigido.

A ausência de cookie, porém, não basta, e a revisão mostrou por quê. Os cookies de sessão são declarados com `SameSite=lax`, de modo que nunca viajaram num envio cross-site; quem barrava um formulário de terceiro apontado para a entrada, o cadastro ou a recuperação de senha era o cookie de proteção, que é estrito. Com apenas a ausência de cookie, a isenção reabriria a entrada forçada: a página do atacante faria o navegador da vítima entrar na conta dele, e a vítima seguiria usando o site achando que é a sua.

Por isso a segunda condição: o corpo precisa ser JSON. Um formulário cross-site só consegue emitir os três tipos que dispensam verificação prévia, e um envio por script de outra origem com JSON depende de uma aprovação que esta borda não concede. Isso não é o CORS sustentando a isenção do cliente nativo, e a distinção importa: é a regra do navegador fechando a única porta pela qual um navegador atacaria. Cliente nativo não é atacante de falsificação de requisição, porque esse ataque precisa, por definição, do navegador da vítima.

A primeira versão desta regra exigia um `Authorization: Bearer` presente, e a revisão mostrou duas consequências. A primeira é que o aplicativo não conseguia fazer login, porque ali ele ainda não tem token algum. A segunda é que um cabeçalho de lixo bastava para remover o CSRF de rota pública de escrita, já que a presença nunca era validada. A regra atual não tem nenhum dos dois problemas.

São quatro os cookies que contam, e o quarto é o do painel administrativo, cujas rotas moram no mesmo aplicativo e passam pelo mesmo middleware. A verificação é pela presença da chave e não pelo valor: um cabeçalho com a mesma chave repetida e vazia faz o analisador guardar a última ocorrência, e o valor vazio apagaria do teste uma credencial que está no jar.

O CORS não sustenta esta isenção e não é o argumento. A allowlist estrita permanece e é útil, mas cliente nativo não passa por CORS, e tratar isso como a garantia seria descrever a proteção errada.

Credencial inválida continua morrendo na autenticação, com 401. O CSRF não pode responder antes dela: um 403 de cabeçalho ausente no lugar do 401 esconde o motivo real de quem depura um login.

## Renovação

O refresh token viaja em `Authorization: Bearer` quando não há cookie de refresh. O cookie tem precedência; o cabeçalho é a segunda fonte, nunca um atalho. A escolha foi imposta pela medição, não pelo gosto: no momento da renovação o app não tem Bearer de sessão para apresentar, porque o access token é justamente o que expirou, e sem Bearer nenhum a requisição não satisfaz a condição acima e morre no CSRF antes de alcançar o handler. Pôr a credencial no cabeçalho resolve isso pela porta da frente e dispensa um modelo de corpo novo, que traria junto risco de 422 nos dois clientes web, que enviam JSON com corpo vazio.

A rotação e a detecção de reapresentação permanecem inteiras e não foram tocadas. Quem renova pelo cabeçalho apresenta o mesmo token opaco, sofre a mesma rotação, e um replay revoga tudo do usuário exatamente como no caminho por cookie. Isso é condição de saída da fase, não efeito colateral esperado.

## Um token, não dois

O app carrega apenas o access token. A resolução de identidade das rotas de dados passa a aceitar tanto o token de dashboard quanto o access JWT. Não é ampliação de privilégio: os dois são assinados pelo mesmo segredo, apontam para o mesmo usuário, carregam o mesmo identificador de sessão e continuam sujeitos à mesma revogação. O access token é o mais curto dos dois, então aceitar quinze minutos onde doze horas já valiam não afrouxa nada. O que se evita é obrigar um cliente sem cookie jar a guardar, rotacionar e renovar dois segredos para a mesma sessão.

## Sair também precisa funcionar

A leitura do token de acesso passa a considerar o cabeçalho `Authorization` mesmo quando a rota não declara a dependência que o injeta. Sem isso, encerrar a sessão pelo aplicativo era operação vazia com aparência de sucesso: o token saía nulo, nada era revogado, e a resposta voltava com duzentos. O usuário apertava sair, via confirmação, e a sessão seguia de pé pelos catorze dias do token de renovação. A correção fica na função que todos os chamadores atravessam, não no encerramento de sessão isoladamente.

## Teto de requisições

A chave do teto passa a ser o usuário quando há credencial legível, e continua o endereço de rede quando não há. O motivo é o CGNAT das operadoras móveis, onde uma antena inteira compartilha um endereço e um usuário ativo derrubaria os vizinhos — cenário em que o aplicativo entra por definição.

Três famílias continuam por endereço de rede: as rotas de autenticação, as do painel administrativo e a do link mágico. É onde se adivinha segredo, e onde a credencial sob ataque não é a do token apresentado. As duas últimas ficaram de fora na primeira versão e a revisão as encontrou. O painel, porque seu caminho de entrada não começa com o prefixo de autenticação. O link mágico, porque o código de uso único que ele carrega é credencial de login: adivinhá-lo é tomar a conta, e o teto daquela rota existe por uma medição registrada nela mesma, de duzentas requisições anônimas com código bem formado virando duzentas exclusões no banco. Nos dois casos, com a chave por usuário o teto passava a ser contado pela conta do próprio atacante, e como o cadastro é livre isso daria tantos baldes quantas contas ele quisesse criar.

## O que isto não autoriza

Não autoriza isentar gate de plano, de assinatura ou de acesso com base em cabeçalho enviado pelo cliente, nem usar cabeçalho como autenticação. O `X-PigBank-Client` seleciona o fluxo de entrega da credencial e nada mais: quem chega nele já passou pela autenticação, e o que recebe é a própria credencial, a mesma que sairia no cookie. Ele não participa da decisão de CSRF, não concede acesso e não identifica ninguém. A lição está registrada no `_is_pigbank_app`, onde o User-Agent chegou a conceder acesso e virou brecha justamente por ser escolhido por quem chama.
