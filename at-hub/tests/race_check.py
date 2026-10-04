"""Race check: real people clicking at the same moment, over real HTTP, against a real server.

    venv\\Scripts\\python -m tests.race_check            (or: pytest -m race)

Copies at_hub.db to a throwaway file, starts its own AT-HUB on a spare port, and fires simultaneous requests
from many threads at the clashes that matter -- then checks the books still balance:

  1. last stock   8 people book the same last 100 units        -> exactly one booking, never oversold
  2. double ship  Ship Now clicked 8 times on one shipment     -> ships once, stock leaves once
  3. double bill  8 invoices made from one shipment            -> one invoice
  4. same order   8 people save one order they all loaded      -> one save, 7 "someone else changed it"
  5. payment      the full payment recorded 8 times at once    -> paid once, never overpaid
  6. busy day     24 people doing mixed work for a while       -> no server errors, no "database is locked"

Then: every item's booked qty = what its open shipments hold, on hand = its lots. Your real data is never touched.
"""
import json
import os
import random
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

N = 8


class Api:
    """Minimal HTTP client as the admin (the Builders' interface: get/post/put return JSON, raise on error)."""
    def __init__(self, base, token):
        self.base, self.token = base, token

    def raw(self, method, url, body=None, headers=None):
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + url, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode()
                return r.status, (json.loads(txt) if txt else None)
        except urllib.error.HTTPError as e:
            txt = e.read().decode()
            try:
                return e.code, json.loads(txt)
            except ValueError:
                return e.code, {"detail": txt[:300]}

    def call(self, method, url, json=None):
        code, data = self.raw(method, url, json)
        if code >= 300:
            raise AssertionError(f"{method} {url} -> {code}: {data}")
        return data

    def get(self, url):
        return self.call("GET", url)

    def post(self, url, json=None):
        return self.call("POST", url, json)

    def put(self, url, json=None):
        return self.call("PUT", url, json)


def together(fn, n=N):
    """Run fn(i) in n threads released at the same instant; returns the results."""
    gate = threading.Barrier(n)

    def run(i):
        gate.wait()
        return fn(i)
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(run, range(n)))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> bool:
    from app.services.auth import AuthService
    from tests.builders import Builders

    tmp = Path(tempfile.mkdtemp(prefix="athub-race-"))
    db_file = tmp / "race.db"
    src = sqlite3.connect(ROOT / "at_hub.db")
    dst = sqlite3.connect(db_file)
    src.backup(dst)
    src.close()
    dst.close()
    port = free_port()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"}
    log = open(tmp / "server.log", "w")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    results = []

    def check(name, ok, detail):
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/login.html", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        api = Api(base, AuthService.create_access_token({"sub": "admin"}))
        make = Builders(api)
        for g in ("Bolt", "Nut"):
            api.raw("POST", "/api/stock-items/groups/list", {"name": g})
        print(f"Race check against a throwaway copy on :{port}", flush=True)

        # 1. the last 100 units, 8 people at once
        a = make.item()
        make.stock(a, 100)
        orders = [make.order(lines=[(a, 100, 2)]) for _ in range(N)]
        codes = together(lambda i: api.raw("POST", f"/api/customer-orders/{orders[i]['id']}/shipments",
                                           {"lines": [{"line_id": orders[i]["lines"][0]["id"], "quantity": 100}]})[0])
        it = api.get(f"/api/stock-items/{a['id']}")
        check("last stock", codes.count(200) == 1 and it["booked"] == 100 and it["available"] == 0,
              f"{codes.count(200)} booking(s) went through, booked {it['booked']:g}, available {it['available']:g}")

        # 2. Ship Now double-clicked
        b = make.item()
        make.stock(b, 50)
        o = make.order(lines=[(b, 50, 3)])
        sh = api.post(f"/api/customer-orders/{o['id']}/shipments", {"lines": [{"line_id": o["lines"][0]["id"], "quantity": 50}]})
        api.post(f"/api/shipments/{sh['id']}/confirm-booking")
        api.post(f"/api/shipments/{sh['id']}/pick", {"pick_all": True})
        api.post(f"/api/shipments/{sh['id']}/accept-packing")
        codes = together(lambda i: api.raw("POST", f"/api/shipments/{sh['id']}/ship")[0])
        it = api.get(f"/api/stock-items/{b['id']}")
        check("double ship", codes.count(200) == 1 and it["on_hand"] == 0 and it["booked"] == 0,
              f"{codes.count(200)} ship(s) went through, on hand {it['on_hand']:g} (was 50)")

        # 3. one shipment, 8 invoices at once
        codes = together(lambda i: api.raw("POST", f"/api/invoices/from-shipment/{sh['id']}", {"shipping_charge": 0})[0])
        invs = [x for x in api.get("/api/invoices/") if sh["id"] in (x.get("shipment_ids") or []) and x["status"] != "void"]
        check("double bill", codes.count(200) == 1 and len(invs) == 1, f"{codes.count(200)} created, {len(invs)} live invoice(s) for the shipment")

        # 4. same order saved by 8 people who all loaded the same version
        c = make.item()
        o4 = make.order(lines=[(c, 5, 1)])
        ver = api.get(f"/api/customer-orders/{o4['id']}")["row_version"]
        codes = together(lambda i: api.raw("PUT", f"/api/customer-orders/{o4['id']}", {"notes": f"saved by person {i}"},
                                           headers={"X-Row-Version": str(ver)})[0])
        check("same order", codes.count(200) == 1 and codes.count(409) == N - 1,
              f"{codes.count(200)} saved, {codes.count(409)} told someone else changed it first, other: {[x for x in codes if x not in (200, 409)]}")

        # 5. the full payment recorded 8 times at once
        inv = invs[0]
        api.put(f"/api/invoices/{inv['id']}/status", {"status": "sent"})  # payments go against a sent invoice
        total = api.get(f"/api/invoices/{inv['id']}")["total"]
        codes = together(lambda i: api.raw("POST", f"/api/invoices/{inv['id']}/payments", {"amount": total})[0])
        after = api.get(f"/api/invoices/{inv['id']}")
        check("payment", codes.count(200) == 1 and abs(after["amount_paid"] - total) < 0.005,
              f"{codes.count(200)} payment(s) taken, paid {after['amount_paid']:.2f} of {total:.2f}")

        # 6. a busy day: 24 people, mixed reads and writes
        d = make.item()
        make.stock(d, 10000)
        cust = make.customer()
        errors, statuses = [], []

        def worker(i):
            rnd = random.Random(i)
            for _ in range(10):
                op = rnd.choice(["read", "read", "order", "book", "save"])
                try:
                    if op == "read":
                        code, _ = api.raw("GET", rnd.choice(["/api/customer-orders/", "/api/shipments/", "/api/stock-items/", "/api/invoices/"]))
                    elif op == "order":
                        code, _ = api.raw("POST", "/api/customer-orders/", {"customer_id": cust["id"], "po_number": f"RACE-{i}-{rnd.random():.6f}",
                                                                           "lines": [{"item_id": d["id"], "quantity": 10, "unit_price": 1}]})
                    elif op == "book":
                        code, oo = api.raw("POST", "/api/customer-orders/", {"customer_id": cust["id"], "po_number": f"RACEB-{i}-{rnd.random():.6f}",
                                                                            "lines": [{"item_id": d["id"], "quantity": 5, "unit_price": 1}]})
                        if code == 200:
                            api.raw("POST", f"/api/customer-orders/{oo['id']}/confirm")
                            code, _ = api.raw("POST", f"/api/customer-orders/{oo['id']}/shipments", {"lines": [{"line_id": oo["lines"][0]["id"], "quantity": 5}]})
                    else:
                        code, _ = api.raw("PUT", f"/api/customer-orders/{o4['id']}", {"notes": f"busy {i}"})
                    statuses.append(code)
                    if code >= 500:
                        errors.append(f"{op} -> {code}")
                except Exception as e:
                    errors.append(f"{op}: {e}")
        t0 = time.time()
        with ThreadPoolExecutor(24) as ex:
            list(ex.map(worker, range(24)))
        log.flush()
        locked = (tmp / "server.log").read_text(errors="ignore").count("database is locked")
        check("busy day", not errors and not locked,
              f"{len(statuses)} requests from 24 people in {time.time() - t0:.1f}s, {len(errors)} server error(s), 'database is locked' x{locked}"
              + (f" -- e.g. {errors[:3]}" if errors else ""))

        # the books still balance
        con = sqlite3.connect(db_file)
        bad_booked = con.execute("""select i.code, i.booked, coalesce(s.q, 0) from stock_items i left join (
              select sl.item_id, sum(sl.quantity) q from shipment_lines sl join shipments s on s.id = sl.shipment_id
              where s.status in ('new', 'ready') group by sl.item_id) s on s.item_id = i.id
            where i.id in (?, ?, ?, ?) and abs(i.booked - coalesce(s.q, 0)) > 1e-6""", (a["id"], b["id"], c["id"], d["id"])).fetchall()
        bad_hand = con.execute("""select i.code, i.on_hand, coalesce(l.q, 0) from stock_items i left join (
              select item_id, sum(quantity) q from lots group by item_id) l on l.item_id = i.id
            where i.id in (?, ?, ?, ?) and abs(i.on_hand - coalesce(l.q, 0)) > 1e-6""", (a["id"], b["id"], c["id"], d["id"])).fetchall()
        con.close()
        check("books balance", not bad_booked and not bad_hand,
              "booked = open shipments and on hand = lots for every item touched" if not (bad_booked or bad_hand)
              else f"booked off: {bad_booked} · on hand off: {bad_hand}")
    finally:
        server.terminate()
        try:
            server.wait(10)
        except Exception:
            server.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(r[1] for r in results)
    print(f"\n{'ALL RACES HANDLED' if ok else 'SOMETHING SLIPPED THROUGH'} ({sum(r[1] for r in results)}/{len(results)})", flush=True)
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
