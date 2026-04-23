from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import get_current_active_user
from app.models import User
from app.services.mrpeasy_client import mrpeasy_client
import time

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/summary")
def get_reports_summary(current_user: User = Depends(get_current_active_user)):
    """
    Aggregate reports for orders, items, customers, and shipments.
    Requires: read permission (all authenticated users)
    """
    try:
        orders = mrpeasy_client.get_customer_orders()
        if not orders:
            return {
                "summary": {
                    "total_orders": 0,
                    "active_orders": 0,
                    "delivered_orders": 0,
                    "cancelled_orders": 0,
                    "partial_orders": 0,
                    "not_shipped_orders": 0,
                    "total_shipped_value": 0,
                    "total_remaining_value": 0,
                    "currency": "$"
                },
                "unfulfilled_items": [],
                "customer_backlog": [],
                "overdue_orders": [],
                "recent_shipments": []
            }

        invoices = mrpeasy_client.get_invoices()
        shipments = mrpeasy_client.get_shipments()

        invoices_by_order = {}
        for invoice in invoices:
            cust_ord_id = invoice.get("cust_ord_id")
            if cust_ord_id:
                invoices_by_order.setdefault(cust_ord_id, []).append(invoice)

        orders_by_id = {}
        for order in orders:
            cust_ord_id = order.get("cust_ord_id")
            if cust_ord_id:
                orders_by_id[cust_ord_id] = order

        now_ts = int(time.time())
        week_ago = now_ts - (7 * 24 * 60 * 60)

        total_orders = len(orders)
        active_orders = 0
        delivered_orders = 0
        cancelled_orders = 0
        partial_orders = 0
        not_shipped_orders = 0

        total_shipped_value = 0
        total_remaining_value = 0
        currency = orders[0].get("currency") or "$"

        invoice_currency = None
        invoice_summary = {
            "dummy": {"count": 0, "total_value": 0},
            "unpaid": {"count": 0, "total_value": 0},
            "paid_partially": {"count": 0, "total_value": 0},
            "paid": {"count": 0, "total_value": 0}
        }

        for invoice in invoices:
            status = invoice.get("status")
            if status == 50:
                continue
            total_value = invoice.get("total_price_cur")
            if total_value is None:
                total_value = invoice.get("total_price") or 0
            inv_currency = invoice.get("currency")
            if inv_currency and not invoice_currency:
                invoice_currency = inv_currency

            if status == 10:
                invoice_summary["dummy"]["count"] += 1
                invoice_summary["dummy"]["total_value"] += total_value
            elif status == 20:
                invoice_summary["unpaid"]["count"] += 1
                invoice_summary["unpaid"]["total_value"] += total_value
            elif status == 30:
                invoice_summary["paid_partially"]["count"] += 1
                invoice_summary["paid_partially"]["total_value"] += total_value
            elif status == 40:
                invoice_summary["paid"]["count"] += 1
                invoice_summary["paid"]["total_value"] += total_value

        unfulfilled_items_map = {}
        customer_backlog = {}
        overdue_orders = []

        for order in orders:
            status = order.get("status")
            if status == 80:
                delivered_orders += 1
            elif status == 90:
                cancelled_orders += 1
            else:
                active_orders += 1

            products = order.get("products", [])
            total_items = len(products)
            fully_shipped_items = 0
            partially_shipped_items = 0
            not_shipped_items = 0

            for product in products:
                quantity = product.get("quantity", 0) or 0
                shipped = product.get("shipped", 0) or 0
                remaining_qty = quantity - shipped

                item_price = product.get("item_price", 0) or 0
                if item_price == 0:
                    cust_ord_id = order.get("cust_ord_id")
                    article_id = product.get("article_id")
                    for inv in invoices_by_order.get(cust_ord_id, []):
                        for inv_product in inv.get("products", []):
                            if inv_product.get("article_id") == article_id:
                                item_price = inv_product.get("item_price", 0) or 0
                                break
                        if item_price:
                            break

                shipped_value = item_price * shipped if item_price and shipped else 0
                remaining_value = item_price * remaining_qty if item_price and remaining_qty else 0

                total_shipped_value += shipped_value
                total_remaining_value += remaining_value

                if remaining_qty > 0:
                    item_code = product.get("item_code") or "N/A"
                    item_title = product.get("item_title") or "N/A"
                    entry = unfulfilled_items_map.get(item_code)
                    if not entry:
                        entry = {
                            "item_code": item_code,
                            "item_title": item_title,
                            "remaining_qty": 0,
                            "remaining_value": 0
                        }
                        unfulfilled_items_map[item_code] = entry
                    entry["remaining_qty"] += remaining_qty
                    entry["remaining_value"] += remaining_value

                if shipped == 0:
                    not_shipped_items += 1
                elif shipped >= quantity:
                    fully_shipped_items += 1
                else:
                    partially_shipped_items += 1

            if total_items > 0:
                if not_shipped_items == total_items:
                    not_shipped_orders += 1
                elif partially_shipped_items > 0:
                    partial_orders += 1

            if total_items > 0:
                remaining_value_order = 0
                for product in products:
                    quantity = product.get("quantity", 0) or 0
                    shipped = product.get("shipped", 0) or 0
                    remaining_qty = quantity - shipped
                    if remaining_qty <= 0:
                        continue

                    item_price = product.get("item_price", 0) or 0
                    if item_price == 0:
                        cust_ord_id = order.get("cust_ord_id")
                        article_id = product.get("article_id")
                        for inv in invoices_by_order.get(cust_ord_id, []):
                            for inv_product in inv.get("products", []):
                                if inv_product.get("article_id") == article_id:
                                    item_price = inv_product.get("item_price", 0) or 0
                                    break
                            if item_price:
                                break

                    remaining_value_order += item_price * remaining_qty if item_price else 0

                if remaining_value_order > 0:
                    customer_name = order.get("customer_name") or "N/A"
                    entry = customer_backlog.get(customer_name)
                    if not entry:
                        entry = {
                            "customer": customer_name,
                            "order_count": 0,
                            "remaining_value": 0
                        }
                        customer_backlog[customer_name] = entry
                    entry["order_count"] += 1
                    entry["remaining_value"] += remaining_value_order

            delivery_date = order.get("actual_delivery_date") or order.get("delivery_date") or order.get("expected_delivery_date")
            if delivery_date:
                remaining_order_qty = sum(
                    (p.get("quantity", 0) or 0) - (p.get("shipped", 0) or 0)
                    for p in products
                )
                if remaining_order_qty > 0 and int(delivery_date) < now_ts:
                    overdue_orders.append({
                        "code": order.get("code"),
                        "reference": order.get("reference"),
                        "customer_name": order.get("customer_name"),
                        "delivery_date": delivery_date,
                        "remaining_value": remaining_value_order
                    })

        unfulfilled_items = sorted(
            unfulfilled_items_map.values(),
            key=lambda x: x["remaining_qty"],
            reverse=True
        )[:50]

        customer_backlog_list = sorted(
            customer_backlog.values(),
            key=lambda x: x["remaining_value"],
            reverse=True
        )[:50]

        overdue_orders = sorted(overdue_orders, key=lambda x: x.get("delivery_date") or 0)[:50]

        recent_shipments = []
        for shipment in shipments:
            shipped_date = shipment.get("shipped_date")
            if shipped_date and int(shipped_date) >= week_ago:
                cust_ord_id = shipment.get("cust_ord_id")
                order = orders_by_id.get(cust_ord_id, {})
                recent_shipments.append({
                    "shipment_code": shipment.get("code"),
                    "shipped_date": shipped_date,
                    "order_code": order.get("code"),
                    "customer_name": order.get("customer_name")
                })

        recent_shipments = sorted(recent_shipments, key=lambda x: x.get("shipped_date") or 0, reverse=True)[:50]

        return {
            "summary": {
                "total_orders": total_orders,
                "active_orders": active_orders,
                "delivered_orders": delivered_orders,
                "cancelled_orders": cancelled_orders,
                "partial_orders": partial_orders,
                "not_shipped_orders": not_shipped_orders,
                "total_shipped_value": round(total_shipped_value, 2),
                "total_remaining_value": round(total_remaining_value, 2),
                "currency": currency
            },
            "invoice_summary": {
                "currency": invoice_currency or currency,
                "dummy": {
                    "count": invoice_summary["dummy"]["count"],
                    "total_value": round(invoice_summary["dummy"]["total_value"], 2)
                },
                "unpaid": {
                    "count": invoice_summary["unpaid"]["count"],
                    "total_value": round(invoice_summary["unpaid"]["total_value"], 2)
                },
                "paid_partially": {
                    "count": invoice_summary["paid_partially"]["count"],
                    "total_value": round(invoice_summary["paid_partially"]["total_value"], 2)
                },
                "paid": {
                    "count": invoice_summary["paid"]["count"],
                    "total_value": round(invoice_summary["paid"]["total_value"], 2)
                }
            },
            "unfulfilled_items": unfulfilled_items,
            "customer_backlog": customer_backlog_list,
            "overdue_orders": overdue_orders,
            "recent_shipments": recent_shipments
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate reports summary: {str(e)}"
        )
