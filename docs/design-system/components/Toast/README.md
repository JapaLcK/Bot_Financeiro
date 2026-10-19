Aviso curto e passageiro: um card elevado com ícone de tom e uma frase, de `app/src/ui/componentes/Toast.tsx`.

**Quem fornece:** `message` e `tone` (`sucesso`, `info`, `aviso`, `erro`).

No app, um provider mostra **um toast por vez** por 3 segundos, fixo a 16px das bordas laterais e de baixo, e entra subindo 24px em 240ms. Este componente é só o visual: quem usa cuida da posição e do tempo. Mensagem de uma linha ("Gasto salvo."). Erro que exige ação é `Banner`, não toast.
