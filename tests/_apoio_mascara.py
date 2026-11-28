"""Mundo dos testes da ocultação de nomes para o leitor (PR 3d): conta de condomínio com lançamentos em
`salários` (grupo pessoal), `mensalidades` (receitas) e `água` (funcionais), em todas as pernas que a leitura junta:
conta, cartão (à vista e parcelado), gasto fixo, receita fixa e boleto."""
from datetime import date, timedelta

import db
from db.bills import create_boleto
from db.cards import add_credit_purchase, add_credit_purchase_installments, create_card
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income
from scripts.ativar_conta_condominio import ativar

# O que NÃO pode sair para o leitor, em lugar nenhum do JSON serializado.
PROIBIDO = ("João", "Joao", "Maria", "CPF", "123.456", "12345678900", "faxineira", "Faxineira", "José", "Jose")
VISIVEL = "Sabesp"

DESC_SALARIO = "Pix para João da Silva CPF 123.456.789-00"
DESC_MENSALIDADE = "Mensalidade apto 101 Maria"


def condominio(uid: int) -> dict:
    """Liga o condomínio em `uid` e lança dados com nome em todo canto. `Sabesp` (água) é o que PODE aparecer."""
    ativar(uid)
    add = db.add_launch_and_update_balance
    add(uid, "despesa", 1500.0, "João da Silva", DESC_SALARIO, "salários")
    add(uid, "receita", 450.0, "Maria", DESC_MENSALIDADE, "mensalidades")
    add(uid, "despesa", 320.0, VISIVEL, "Sabesp conta de água", "água")
    hoje = date.today()
    card = create_card(uid, "Cartão do condomínio", 5, 15)
    add_credit_purchase(uid, card, 800.0, "salários", "13º do José CPF 123.456.789-00", hoje)
    add_credit_purchase_installments(uid, card, 600.0, "mensalidades", "Acordo Maria apto 101", hoje, 3)
    add_credit_purchase(uid, card, 90.0, "água", "Sabesp cartão", hoje)
    create_recurring_expense(uid, "Maria faxineira", 900.0, "salários", min(hoje.day, 28), "account", notes="CPF 123.456.789-00")
    create_recurring_expense(uid, "Sabesp mensal", 300.0, "água", min(hoje.day, 28), "account")
    create_recurring_income(uid, "Mensalidade Maria apto 101", 450.0, "mensalidades", min(hoje.day, 28), notes="CPF 123.456.789-00")
    create_boleto(uid, "Salário do José", 700.0, hoje + timedelta(days=5), "salários")
    create_boleto(uid, "Sabesp boleto", 100.0, hoje + timedelta(days=6), "água")
    return {"uid": uid, "card": card}
