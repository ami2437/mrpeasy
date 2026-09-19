# MRPeasy Custom Portal — Documentation

Consolidated documentation for the project. Component-level `README.md` files
(root, `backend/`, `backend-fastapi/`, `frontend/`) remain in place as entry
points; everything else lives here.

| Doc | Covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Database models, MRPeasy API client, order/invoice data structures and data flow |
| [PACKING_SLIP.md](PACKING_SLIP.md) | Packing slip / shipment labeling system: schema, API, frontend, testing |
| [RBAC.md](RBAC.md) | Authentication & role-based access control: roles, API, quickstart, deployment |
| [SETUP.md](SETUP.md) | Backend Python environment setup and quick-start commands |
| [SECURITY.md](SECURITY.md) | Verification that the MRPeasy integration is read-only |
| [ROLLBACK_PLAN.md](ROLLBACK_PLAN.md) | Rollback/data-protection plan for the label tools feature |

## Historical note

A batch of one-off bug-investigation and status-report documents (e.g. the
"-NUT items missing from invoices" investigation, RBAC/packing-slip
completion reports, duplicate documentation indexes) were removed during a
documentation cleanup. They described already-resolved, point-in-time
investigations rather than lasting system behavior; the durable findings were
folded into the docs above where still relevant.
