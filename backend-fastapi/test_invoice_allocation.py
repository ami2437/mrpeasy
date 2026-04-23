import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

from app.routes.customer_orders import _allocate_invoice_quantities
from app.routes.invoicing import _has_zero_selling_price


def test_allocate_invoice_quantities_only_to_delivered_duplicate_lines():
    products = [
        {'article_id': 1, 'item_code': '76003', 'quantity': 500, 'shipped': 0},
        {'article_id': 1, 'item_code': '76003', 'quantity': 500, 'shipped': 500},
        {'article_id': 2, 'item_code': '76003-NUT', 'quantity': 500, 'shipped': 0},
        {'article_id': 2, 'item_code': '76003-NUT', 'quantity': 500, 'shipped': 500},
    ]
    order_invoices = [
        {
            'code': 'Inv-9601564',
            'status_txt': 'Unpaid',
            'products': [
                {'article_id': 1, 'item_code': '76003', 'quantity': 500},
                {'article_id': 2, 'item_code': '76003-NUT', 'quantity': 500},
            ],
        }
    ]

    allocations = _allocate_invoice_quantities(products, order_invoices)

    assert allocations[0] == []
    assert allocations[2] == []
    assert allocations[1] == [
        {
            'invoice_code': 'Inv-9601564',
            'invoice_status': 'Unpaid',
            'quantity_invoiced': 500.0,
        }
    ]
    assert allocations[3] == [
        {
            'invoice_code': 'Inv-9601564',
            'invoice_status': 'Unpaid',
            'quantity_invoiced': 500.0,
        }
    ]


def test_allocate_invoice_quantities_respects_available_invoice_quantity():
    products = [
        {'article_id': 1, 'item_code': '76003', 'quantity': 500, 'shipped': 300},
        {'article_id': 1, 'item_code': '76003', 'quantity': 500, 'shipped': 200},
        {'article_id': 1, 'item_code': '76003', 'quantity': 500, 'shipped': 100},
    ]
    order_invoices = [
        {
            'code': 'Inv-1',
            'status_txt': 'Unpaid',
            'products': [
                {'article_id': 1, 'item_code': '76003', 'quantity': 500},
            ],
        }
    ]

    allocations = _allocate_invoice_quantities(products, order_invoices)

    assert allocations[0] == [
        {
            'invoice_code': 'Inv-1',
            'invoice_status': 'Unpaid',
            'quantity_invoiced': 300.0,
        }
    ]
    assert allocations[1] == [
        {
            'invoice_code': 'Inv-1',
            'invoice_status': 'Unpaid',
            'quantity_invoiced': 200.0,
        }
    ]
    assert allocations[2] == []


def test_has_zero_selling_price_only_when_both_unit_and_total_are_zero():
    assert _has_zero_selling_price(0, 0, 500) is True
    assert _has_zero_selling_price(2.5, 0, 500) is False
    assert _has_zero_selling_price(0, 1250, 500) is False
