# Role-Based Access Control (RBAC)

## Overview

The portal uses JWT-based authentication with a three-tier role system to
protect API endpoints while keeping the MRPeasy integration itself read-only.

- **Auth service** (`app/services/auth.py`): bcrypt password hashing, JWT
  creation/validation, user authentication/creation, role checks.
- **Auth routes** (`app/routes/auth.py`): registration, login, profile, admin
  user management.
- **Auth middleware** (`app/dependencies.py`): extracts/validates the JWT and
  enforces role-based permissions on protected routes.
- **Models**: `User` (username, email, hashed_password, role, is_active,
  timestamps) and `Role`, auto-created on startup.

## Roles & permissions

| Permission | Admin | Editor | Viewer |
|---|---|---|---|
| Read data | ✅ | ✅ | ✅ |
| Modify local data | ✅ | ✅ | ❌ |
| Delete local data | ✅ | ❌ | ❌ |
| Sync from MRPeasy | ✅ | ✅ | ❌ |
| Manage users/roles | ✅ | ❌ | ❌ |

- **Admin** — full access: system administrators, portal managers.
- **Editor** — read + write, no delete/user management: production
  supervisors, order managers, data entry staff.
- **Viewer** — read-only: QA, sales, reporting/audit.

Protected endpoints: customer orders, sync operations, stock items,
manufacturing orders. All require authentication; write/delete/sync/user
management are additionally role-gated.

## API reference

### `POST /api/auth/register`
```json
{ "username": "john_doe", "email": "john@example.com", "password": "secure_password", "full_name": "John Doe", "role": "viewer" }
```
200 on success, 400 if username/email exists.

### `POST /api/auth/login`
```json
{ "username": "john_doe", "password": "secure_password" }
```
Returns `{ "access_token": "...", "token_type": "bearer", "user": {...} }`.
401 invalid credentials, 403 inactive user.

### `GET /api/auth/me` / `PUT /api/auth/me`
Requires `Authorization: Bearer <token>`. Returns/updates the current user
profile (email, full_name, role).

### `GET /api/auth/users` (admin only)
Lists all users.

### `PUT /api/auth/users/{id}` (admin only)
Updates a user's role/status, e.g. `{ "role": "editor" }`.

JWT details: HS256, 24-hour expiration by default (configurable), includes
username + expiry. Passwords are hashed with bcrypt (auto-salted via
passlib), never stored in plaintext.

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload

# Register an admin
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@local.dev","password":"AdminPass123!","role":"admin"}'

# Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"AdminPass123!"}'

# Use the token
curl -X GET "http://localhost:8000/customer-orders/" -H "Authorization: Bearer $TOKEN"
```

React frontend integration:
```javascript
async function login(username, password) {
  const { data } = await axios.post('http://localhost:8000/api/auth/login', { username, password });
  localStorage.setItem('token', data.access_token);
  localStorage.setItem('user', JSON.stringify(data.user));
  return data.user;
}

function authHeader() {
  return { Authorization: `Bearer ${localStorage.getItem('token')}` };
}
```

## Test scenarios

Covers each role against read/write/delete/sync/user-management endpoints,
e.g.: viewer can `GET` data but gets 403 on write/sync/user endpoints; editor
can write/sync but gets 403 on delete/user management; admin passes all.
Standard flow per test case: register → login → call endpoint with the
token → assert expected status code.

## Deployment checklist

**Security**
- [ ] Replace `secret_key` in `app/config/settings.py` with a strong random
      value (32+ chars) via environment variable.
- [ ] Restrict CORS origins to the production domain.
- [ ] Enable HTTPS/TLS.
- [ ] Consider rate limiting on auth endpoints.

**Database**
- [ ] Back up the database before deploying.
- [ ] Create the initial admin user.
- [ ] Set up automated backups.

**Testing**
- [ ] Registration, login, token generation/expiration.
- [ ] Protected endpoints under each role.
- [ ] Invalid credential handling.

**Operations**
- [ ] Monitoring, logging, error alerts, backup/recovery documented.
