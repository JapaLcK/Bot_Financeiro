"""Corpus do Tester (passada 2, `corp_gate`: 63 comandos e 187 hedgeadas) e do Manager (`frases`: hedge no fim,
cancelamento, pedido de confirmação, citação, infinitivo como sujeito, pergunta sem "?", "abre"). Dado puro, para
`tests/test_metas_gate.py` e `tests/test_wa_metas_escrita_corpus.py`. Frases do Manager são "treino": a gramática
NÃO foi ajustada frase a frase."""
COMANDOS = r"""cria uma meta de 5000 pra viagem
cria uma meta de R$ 5.000 pra viagem
Cria uma meta de 5000 pra viagem
CRIA UMA META DE 5000 PRA VIAGEM
crie uma meta de 5000 pra viagem
criar uma meta de 5000 pra viagem
criar meta de 5000
criar meta viagem 5000
cria meta de 5000 pra viagem
cria a meta Viagem de 5 mil
cria uma meta de cinco mil pra viagem
cria uma meta de 5k pra viagem
cria uma meta de 20 mil pra casa até dezembro de 2027
faz uma meta de R$ 20.000 pra casa até dezembro de 2027
faça uma meta de 3000 pro carro
registra uma meta chamada Carro de 30 mil
registre uma meta de 2000
cadastra uma meta de 1500 pro notebook
cadastrar meta de 1500
pode criar uma meta de 1500 pro notebook
pode criar uma meta de 5000 pra mim
da pra criar uma meta de 5000
por favor cria uma meta de 5000
por favor, cria uma meta de 5000 pra viagem
abre uma meta de 5000
abre uma meta pra viagem de 5000
criar uma meta de 5000 pra viagem de férias
cria uma meta de 5 mil pra reserva de viagem
cria minha reserva de emergência
cria uma reserva de emergência de 6 meses
cria a reserva de emergência
cria uma meta nova de 5000
faz uma meta nova de 5000 pra viagem
cria uma meta de 5.000,00 pra viagem
cria uma meta de 5000 reais pra viagem
cria uma meta de 5000 pro natal
cria uma meta de 10000 chamada Casa
cria uma meta Casa de 10000 até 2028
cria uma metá de 5000 pra viagem
cria uma meta de 5000 pra viagem pf
cria uma meta de 5000 pra viagem por favor
cria a meta viagem 5000
cria meta 5000 viagem
cria uma meta de mil reais pra viagem
cria uma meta de 5000 pra viagem, fonte tudo investido
cria uma meta de 5000 pra viagem acompanhando o nubank
cria uma meta de 5000 pra viagem de 2027
cadastre uma meta de 8000 pra moto
cria uma meta de 5000 pra viagem 😀
criar uma meta de 700 pro curso
cria uma meta pra comprar um carro de 40 mil
cria uma meta de 500 por mes pra viagem
faz a meta da viagem de 5000
registra a meta viagem de 5000
crie a meta Casa de 10 mil
fazer uma meta de 2500 pra festa
criar uma meta de 3 mil pro fim do ano
cria uma meta de r$5000 pra viagem
cria uma meta de 5000 pra viagem, valeu
cria uma meta pro casamento de 30 mil
cria uma meta chamada Viagem Europa de 15000
cria uma meta Viagem de 5000 e prazo junho de 2027
cria uma meta de 5000""".split("\n")

HEDGEADAS = r"""cria uma meta de 5000 pra viagem?
se eu pedir pra criar uma meta de 5000 você cria?
cria uma meta de 5000 pra viagem, mas só se der
não cria uma meta de 5000
nao crie meta nenhuma
não cria meta
cria uma meta de 5000 pra viagem... esquece
será que cria uma meta de 5000?
pode criar uma meta de 5000 pra mim? é só curiosidade
quando eu falo 'cria uma meta de 5000' o que acontece?
minha amiga disse: cria uma meta de 5000
"cria uma meta de 5000"
cria uma meta de 5000 pra viagem ou não
cria uma meta de 5000 pra viagem caso eu consiga
cria uma meta de 5000 pra viagem talvez
e se eu criasse uma meta de 5000?
queria uma meta de 5000 pra viagem
quero uma meta de 5000 pra viagem
preciso de uma meta de 5000
acho que preciso de uma meta de 5000
gostaria de uma meta de 5000
me ajuda a criar uma meta de 5000
me ajuda a criar uma meta
como faço pra criar uma meta?
como criar uma meta?
como cria uma meta de 5000?
dá pra criar uma meta de 5000?
da pra criar uma meta de 5000?
posso criar uma meta de 5000?
criei uma meta de 5000
criou uma meta de 5000
minha amiga criou uma meta de 10 mil
eu criei uma meta ontem
ja criei a meta viagem
criar uma meta de 5000 é boa ideia?
criar uma meta de 5000 vale a pena?
uma meta de 5000 seria boa pra viagem?
registrei 100 de receita pra meta 2028
registra um depósito de 100 na meta 2028
registra 100 na meta 2028
registra uma receita de 100 na meta
registra um gasto de 50 na meta
registra um pix de 100 pra meta
registra pagamento da fatura meta
faz um depósito de 100 na meta 2028
faz um resgate de 100 da meta 2028
faz um aporte de 100 na meta
faz um pix de 50 pra meta
faz o pagamento da fatura da meta
faz a conta da meta pra mim
faz um relatório da meta
faz um resumo das minhas metas
cria uma caixinha meta 2028
criar caixinha meta casa
cria um cofrinho pra meta
cria um orçamento de meta
cria uma categoria meta
cria uma categoria chamada metas
cria um cartão meta
cria um investimento na meta
cria um lançamento de 50 na meta 2028
cria um gasto de 50 na meta
cria uma despesa meta
cria uma receita de 100 pra meta
cria uma meta de gastos de 500
cria uma meta de gasto mensal de 2000
cria uma meta de orçamento
cria uma meta de investimento de 5000
cria uma meta no cartão
apaga a meta Casa nova
apagar a meta viagem
exclui a meta viagem
muda a meta da viagem pra 6000
altera a meta viagem
edita a meta casa
aumenta a meta viagem
mostra minhas metas
minhas metas
lista as metas
quero ver minhas metas
cadê minha meta?
quanto falta pra minha meta?
tô no ritmo da meta?
se eu criar uma meta de 5000 o que acontece?
caso eu crie uma meta de 5000, você avisa?
sera que eu crio uma meta de 5000?
seria bom criar uma meta de 5000
talvez eu crie uma meta de 5000
nao quero criar uma meta
não vou criar meta nenhuma
nunca cria meta sem eu pedir
cria sozinha uma meta de 5000?
voce cria meta sozinho?
você cria metas?
você pode criar uma meta?
tem como criar uma meta?
quero criar uma meta
quero criar uma meta de 5000
preciso criar uma meta de 5000
vou criar uma meta de 5000
vou pedir pra criar uma meta
mensagem: cria uma meta de 5000
escrevi cria uma meta de 5000
ele falou cria uma meta de 5000
cria uma meta de 5000 pra viagem, se der
cria uma meta de 5000 pra viagem se der
cria uma meta de 5000 pra viagem nao sei
cria uma meta de 5000 pra viagem nao
cria uma meta de 5000 pra viagem não precisa
não precisa criar uma meta de 5000
deixa pra lá, cria uma meta de 5000 só amanhã
cria uma meta de 5000 só amanhã
amanhã cria uma meta de 5000
depois cria uma meta de 5000
cria uma meta de 5000 depois
cria uma meta de 5000 pra viagem quando eu mandar
espera, cria uma meta de 5000
pensando em criar uma meta de 5000
ia criar uma meta de 5000
cria uma meta de 5000 pra viagem, ou melhor, de 6000
cria uma meta ou uma caixinha de 5000
cria uma meta, uma caixinha ou um investimento
cria uma meta e deposita 100
cria uma meta de 5000 e apaga a meta Casa
cria uma meta de 5000 pra viagem e outra de 3000 pro carro
cria uma meta de 5000 pra viagem e registra um gasto de 50
cria uma meta de 5000 pra viagem e paga a fatura
e se eu criasse a meta viagem de 5000?
me cria uma meta de 5000
me crie uma meta de 5000
crie pra mim uma meta de 5000
voce pode criar uma meta de 5000
você podia criar uma meta de 5000
cria, por favor, uma meta de 5000
eu queria que voce criasse uma meta de 5000
quero que voce crie uma meta de 5000
quero que cria uma meta de 5000
gostaria que criasse uma meta de 5000
metas: criar viagem 5000
meta: criar viagem 5000
meta viagem 5000
nova meta de 5000
uma meta de 5000
meta de 5000 pra viagem
criar viagem 5000
cria viagem de 5000
cria reserva
cria reserva de emergencia
cria uma reserva de 5000 no nubank
cria uma reserva na caixinha
cria uma reserva de dinheiro
cria uma reserva de valor
reserva de emergência: cria
criar reserva do banco
cria uma meta de 5000 pra viagem /apaga
cria uma meta de 5000 pra viagem\napaga a meta casa
cria uma meta de 5000 pra viagem\n\nesquece isso
cria uma meta de 5000 pra viagem mas antes me diz quanto eu gastei
cria uma meta de 5000 pra viagem, depois te falo
cria uma meta de 5000 pra viagem (teste)
cria uma meta de 5000 pra viagem (só um teste)
cria uma meta de 5000 pra viagem, teste
isso é um teste: cria uma meta de 5000
teste: cria uma meta de 5000
exemplo: cria uma meta de 5000
por exemplo, cria uma meta de 5000
tipo cria uma meta de 5000
tipo, se eu falar cria uma meta de 5000 voce cria
faz de conta que cria uma meta de 5000
imagina que cria uma meta de 5000
simula: cria uma meta de 5000
simule criar uma meta de 5000
simula criar uma meta de 5000
só simulação: cria uma meta de 5000
sem gravar, cria uma meta de 5000
cria uma meta de 5000 sem gravar
cria uma meta de 5000 sem salvar
cria uma meta de 5000 sem confirmar
nao grava ainda, cria uma meta de 5000
cria uma meta de 5000 mas nao grava
cria uma meta de 5000 pra viagem mas ainda nao
cria uma meta de 5000 pra viagem sem pressa
cria uma meta de 5000 pra viagem quando der
cria uma meta de 5000 pra viagem quando puder
cria uma meta de 5000 pra viagem se possivel
cria uma meta de 5000 pra viagem, se for possível
cria uma meta de 5000 pra viagem, se puder""".split("\n")

V = 'cria uma meta de 5000 pra viagem'
MGR_HEDGE = {
 'hedge_cauda': ['@V, mas me diz se vale a pena', '@V só pra ver', '@V e depois me explica', '@V, pode ser', '@V, sei lá', '@V sla', '@V, o que acha', '@V, que tal', '@V, vale a pena', '@V, faz sentido', '@V, acho', '@V quem sabe', '@V, nem sei', '@V to na duvida', '@V rola', '@V, consegue', '@V é muito', '@V, é exagero', '@V ne', '@V 🤔', '@V 🤷\u200d♂️', '@V hipoteticamente', '@V em tese', '@V futuramente', '@V mais tarde', '@V daqui a pouco', '@V semana que vem eu confirmo'],
 'cancela_cauda': ['@V, ah deixa', '@V, deixa pra la', '@V, cancela', '@V, desisti', '@V, ignora', '@V, nada disso', '@V, mentira', '@V kkkk brincadeira', '@V zoeira', '@V n', '@V, n precisa', '@V nn', '@V, errei', '@V, para', '@V pfv nao'],
 'pede_confirmacao': ['@V, confirma comigo', '@V me pergunta primeiro', '@V, mas confirma antes', '@V e me mostra antes de salvar', '@V rascunho'],
 'citacao_terceiro': ['@V, disse minha mãe', '@V foi o que ela falou', '@V — meu pai que mandou eu falar isso', '@V, o gerente sugeriu', '@V, amor', '@V ela me disse isso ontem'],
 'infinitivo_sujeito': ['criar uma meta de 5000 pra viagem é fácil', 'criar uma meta de 5000 pra viagem é uma boa', 'criar uma meta de 5000 pra viagem ajuda', 'fazer uma meta de 5000 pra viagem é importante', 'abrir uma meta de 5000 pra viagem me ajudaria', 'criar meta de 5000 pra viagem foi a melhor coisa que fiz', 'cadastrar uma meta de 5000 pra viagem e dificil', 'registrar uma meta de 5000 deve ser bom'],
 'pergunta_sem_interrogacao': ['da pra criar uma meta de 5000 pra viagem', 'pode criar meta pelo whatsapp', 'da pra criar meta de 5000 aqui', 'podia criar uma meta de 5000 pra viagem', '@V？', '@V ¿', '@V sera q da'],
 'abre_le': ['abre a meta viagem', 'abre minha meta da viagem de 5000', 'abre a meta de 5000'],
}
MGR_HEDGE = {k: [x.replace("@V", V) for x in v] for k, v in MGR_HEDGE.items()}
MGR_LEGIT = {
 'informal': ['cria ai uma meta de 5000 pra viagem', 'cria uma meta de 5000 pra viagem ai', 'cria uma meta de 5000 pra viagem hein', 'cria uma meta de 5000 pra viagem então', 'cria uma meta de 5000 pra viagem blz', 'bora, cria uma meta de 5000 pra viagem', 'cria uma meta de 5 conto pra viagem', 'cria uma meta de 5k pra trip', 'cria uma mta de 5000 pra viagem', 'cira uma meta de 5000'],
 'formal': ['crie uma meta de R$ 5.000,00 para a viagem', 'Por favor, crie uma meta de 5000 reais para viagem', 'Crie, por gentileza, uma meta de 5000', 'cadastre uma meta de 5000 para viagem com prazo em dezembro'],
 'maiusc_emoji': ['CRIA UMA META DE 5 MIL PRA VIAGEM 🚀', 'Cria Uma Meta De 5000 Pra Viagem ✈️', 'cria uma meta de 5000 pra viagem 🙏'],
 'extenso_k': ['cria uma meta de cinco mil reais pra viagem', 'cria uma meta de 5 mil e quinhentos pra viagem', 'cria uma meta de 2,5k pro celular', 'cria uma meta de dez mil pra casa'],
 'duas_metas': ['cria uma meta de 5000 pra viagem e uma de 3000 pro carro', 'cria uma meta de 5000 pra viagem, também uma de 3000 pro carro'],
 'frase_longa': ['oi piggy tudo bem cria uma meta de 5000 pra viagem', 'bom dia, cria uma meta de 5000 pra viagem', 'cria uma meta de 5000 pra viagem pra julho do ano que vem que eu vou pra europa com minha familia'],
}

# Tester passada 3 (`corpus3b`, 59 dirigidas ao slot do nome e ao qualificador de valor). Treino: a gramática não foi ajustada frase a frase.
TESTER3 = [
    'cria uma meta tipo 5000',
    'cria uma meta uns 5000',
    'cria uma meta de uns 5000',
    'cria uma meta cerca de 5000',
    'cria uma meta aproximadamente 5000',
    'cria uma meta por volta de 5000',
    'cria uma meta no maximo 5000',
    'cria uma meta pelo menos 5000',
    'cria uma meta ate 5000',
    'cria uma meta menos de 5000',
    'cria uma meta quase 5000',
    'cria uma meta perto de 5000',
    'cria uma meta algo como 5000',
    'cria uma meta de tipo 5000',
    'cria uma meta de no maximo 5 mil',
    'cria uma meta de pelo menos 5 mil',
    'cria uma meta de ate 5 mil',
    'cria uma meta digamos 5000',
    'cria uma meta imagina 5000',
    'cria uma meta supondo 5000',
    'cria uma meta quem sabe 5000',
    'cria uma meta de quem sabe 5000',
    'cria uma meta jamais 5000',
    'cria uma meta nunca 5000',
    'cria uma meta ficticia de 5000',
    'cria uma meta hipotetica de 5000',
    'cria uma meta imaginaria de 5000',
    'cria uma meta simulada de 5000',
    'cria uma meta mentirinha de 5000',
    'cria uma meta exemplo de 5000',
    'cria uma meta fake de 5000',
    'cria uma meta kkk 5000',
    'cria uma meta ok 5000',
    'cria uma meta blz 5000',
    'cria uma meta de 5000 pra simulação',
    'cria uma meta de 5000 pra exemplo',
    'cria uma meta de 5000 pra mentirinha',
    'cria uma meta de 5000 pra brincadeira',
    'cria uma meta de 5000 pra zoeira',
    'cria uma meta de 5000 pra sempre',
    'cria uma meta de 5000 pra ontem',
    'cria uma meta de 5000 pra nunca',
    'cria uma meta de 5000 chamada ficticia',
    'cria uma meta de 5000 chamada hipotese',
    'cria uma meta de 5000 chamada nada',
    'cria uma meta de 5000 pra viagem de mentirinha',
    'cria uma meta de 5000 pra viagem de exemplo',
    'cria uma meta de 5000 pra viagem de brincadeira',
    'cria uma meta de 5000 pra quem',
    'cria a meta 2028',
    'cria uma meta 2030',
    'faz a meta 2027',
    'registra meta 2028',
    'cria a meta de 2028',
    'cria a meta 100',
    'registra meta viagem 5000',
    'faz meta casa 300',
    'registra a meta viagem 100',
    'faz a meta casa 200',
]
