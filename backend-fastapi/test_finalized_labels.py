import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import ShipmentBox
from app.routes.labels import get_finalized_labels


class FinalizedLabelsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "labels-test.db"
        self.engine = create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        ShipmentBox.__table__.create(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

        finalized_at = datetime(2026, 9, 10, 12, 30)
        self.session.add_all(
            [
                ShipmentBox(
                    shipment_code="SHIP-1",
                    customer_order_code="ORDER-1",
                    po_number="PO-1",
                    customer_name="Customer One",
                    job_number="JOB-1",
                    item_code="ITEM-B",
                    item_title="Item B",
                    order_line="1",
                    pack_size=10,
                    box_number=1,
                    quantity_in_box=7,
                    total_quantity=7,
                    lot_codes="not-json",
                    finalized_at=finalized_at,
                ),
                ShipmentBox(
                    shipment_code="SHIP-1",
                    customer_order_code="ORDER-1",
                    po_number="PO-1",
                    customer_name="Customer One",
                    job_number=None,
                    item_code="ITEM-A",
                    item_title="Item A",
                    order_line="2",
                    pack_size=20,
                    box_number=2,
                    quantity_in_box=5,
                    total_quantity=25,
                    lot_codes=json.dumps(["LOT-2"]),
                    finalized_at=finalized_at,
                ),
                ShipmentBox(
                    shipment_code="SHIP-1",
                    customer_order_code="ORDER-1",
                    po_number="PO-1",
                    customer_name="Customer One",
                    job_number=None,
                    item_code="ITEM-A",
                    item_title="Item A",
                    order_line="2",
                    pack_size=20,
                    box_number=1,
                    quantity_in_box=20,
                    total_quantity=25,
                    lot_codes=json.dumps(["LOT-1", "LOT-2"]),
                    finalized_at=finalized_at,
                ),
                ShipmentBox(
                    shipment_code="SHIP-2",
                    customer_order_code="ORDER-2",
                    item_code="ITEM-A",
                    item_title="Other Shipment",
                    order_line="1",
                    pack_size=1,
                    box_number=1,
                    quantity_in_box=1,
                    total_quantity=1,
                ),
            ]
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_returns_ordered_finalized_labels_for_only_requested_shipment(self):
        before_count = self.session.query(ShipmentBox).count()

        result = get_finalized_labels("SHIP-1", self.session)

        self.assertTrue(result["success"])
        self.assertTrue(result["read_only"])
        self.assertTrue(result["finalized"])
        self.assertEqual(result["total_labels"], 3)
        self.assertEqual(
            [(label["item_code"], label["order_line"], label["box_number"]) for label in result["labels"]],
            [("ITEM-A", "2", 1), ("ITEM-A", "2", 2), ("ITEM-B", "1", 1)],
        )
        self.assertTrue(all(label["shipment_code"] == "SHIP-1" for label in result["labels"]))
        self.assertEqual(result["labels"][0]["total_boxes"], 2)
        self.assertEqual(result["labels"][0]["lot_code"], "LOT-1, LOT-2")
        self.assertEqual(result["labels"][0]["customer_order"], "ORDER-1")
        self.assertEqual(result["labels"][0]["reference"], "PO-1")
        self.assertIsNone(result["labels"][0]["job_number"])
        self.assertEqual(result["labels"][2]["lot_code"], "not-json")
        self.assertEqual(self.session.query(ShipmentBox).count(), before_count)

    def test_returns_explicit_unfinalized_result_when_no_boxes_exist(self):
        result = get_finalized_labels("MISSING", self.session)

        self.assertTrue(result["success"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["finalized"])
        self.assertEqual(result["total_labels"], 0)
        self.assertEqual(result["labels"], [])


if __name__ == "__main__":
    unittest.main()
