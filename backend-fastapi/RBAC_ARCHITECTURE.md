# RBAC System Architecture

## Authentication Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Application                       │
│                       (React Frontend)                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    HTTP Requests
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   ┌─────────┐       ┌──────────┐       ┌──────────┐
   │ Register│       │  Login   │       │Protected │
   │ Endpoint│       │ Endpoint │       │Endpoints │
   └────┬────┘       └────┬─────┘       └────┬─────┘
        │                 │                   │
        │    JWT Token    │                   │
        │    Generated    │                   │ Includes Token
        │                 │                   │ in Header
        └─────────────────┼───────────────────┘
                          │
                          ▼
            ┌──────────────────────────┐
            │  FastAPI Application     │
            │  (/docs for Swagger)     │
            └──────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Auth Routes  │  │ Data Routes  │  │ Sync Routes  │
  │ /api/auth/*  │  │ /customer-*  │  │ /sync/*      │
  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                          │
                          ▼
            ┌──────────────────────────┐
            │   Dependencies Layer     │
            │  (Authentication Check)  │
            └──────────────────────────┘
                          │
         ┌────────────────┴────────────────┐
         │                                 │
         ▼                                 ▼
    ┌─────────────┐              ┌──────────────────┐
    │Auth Service │              │ RBAC Service     │
    │- JWT Ops    │              │- Permissions     │
    │- Hashing    │              │- Role Checking   │
    └─────────────┘              └──────────────────┘
         │                                 │
         └────────────────┬────────────────┘
                          │
                          ▼
            ┌──────────────────────────┐
            │    SQLAlchemy ORM        │
            │    (Database Layer)      │
            └──────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
         ▼                ▼                ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │   Users    │  │   Roles    │  │   Orders   │
    │   Table    │  │   Table    │  │   Table    │
    └────────────┘  └────────────┘  └────────────┘
```

## Request Lifecycle

```
1. Client sends credentials to /api/auth/login
   │
   ├─► Credentials validated
   ├─► Password checked against bcrypt hash
   ├─► JWT token generated
   └─► Token sent to client

2. Client stores token and includes in subsequent requests
   │
   ├─► Header: Authorization: Bearer <JWT_TOKEN>
   │
   └─► Request to protected endpoint (e.g., /customer-orders/)

3. FastAPI intercepts request
   │
   ├─► Dependency: get_current_user()
   │   ├─► Extract token from Authorization header
   │   ├─► Decode JWT token
   │   ├─► Verify signature with secret_key
   │   ├─► Check token expiration
   │   └─► Load user from database
   │
   └─► Dependency: require_permission("write")
       ├─► Get current user
       ├─► Check RBACService.can_perform_action()
       ├─► Verify user role has permission
       └─► If authorized: proceed; else: 403 Forbidden

4. Route handler executes
   │
   ├─► Access current_user object
   ├─► Perform requested action
   └─► Return response to client
```

## Component Relationships

```
┌────────────────────────────────────────────────────────┐
│ HTTP Request with JWT Token                            │
│ Authorization: Bearer eyJhbGciOiJIUzI1NiIs...          │
└────────────────┬───────────────────────────────────────┘
                 │
                 ▼
        ┌────────────────────┐
        │ FastAPI Router     │
        │ @router.get("/...")│
        └────────┬───────────┘
                 │
                 ▼
   ┌─────────────────────────────────────┐
   │ Route Dependencies                   │
   │ Depends(get_current_active_user)    │
   │ Depends(require_permission("write")) │
   └──────────┬────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────┐
   │ JWT Token Decoding                   │
   │ • Extract from header                │
   │ • Verify signature                   │
   │ • Check expiration                   │
   └──────────┬────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────┐
   │ AuthService.decode_token()           │
   │ Returns: {"sub": "username", ...}    │
   └──────────┬────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────┐
   │ Load User from Database              │
   │ SELECT * FROM users WHERE username   │
   └──────────┬────────────────────────────┘
              │
              ▼
   ┌─────────────────────────────────────┐
   │ RBACService.can_perform_action()     │
   │ Check permission matrix              │
   │ admin.write = True ✅                │
   │ editor.delete = False ❌             │
   │ viewer.sync = False ❌               │
   └──────────┬────────────────────────────┘
              │
      ┌───────┴────────┐
      │                │
   ✅ │            ❌  │
Authorized         Forbidden
      │                │
      ▼                ▼
   Proceed         Return 403
   with request    with error
```

## Role Permission Model

```
┌──────────────────────────────────────────────────────────┐
│                    RBAC Hierarchy                        │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ADMIN (Full Access)                                     │
│  ├── read        ✅                                      │
│  ├── write       ✅                                      │
│  ├── delete      ✅                                      │
│  ├── sync        ✅                                      │
│  └── manage_users ✅                                     │
│       │                                                  │
│       ├─► Can perform all operations                     │
│       ├─► Can manage user accounts                       │
│       └─► Full system access                            │
│                                                          │
│  EDITOR (Read + Write)                                   │
│  ├── read        ✅                                      │
│  ├── write       ✅                                      │
│  ├── delete      ❌                                      │
│  ├── sync        ✅                                      │
│  └── manage_users ❌                                     │
│       │                                                  │
│       ├─► Can modify data                               │
│       ├─► Can sync MRPeasy                              │
│       └─► Cannot delete or manage users                 │
│                                                          │
│  VIEWER (Read-Only)                                      │
│  ├── read        ✅                                      │
│  ├── write       ❌                                      │
│  ├── delete      ❌                                      │
│  ├── sync        ❌                                      │
│  └── manage_users ❌                                     │
│       │                                                  │
│       ├─► View data only                                │
│       ├─► Cannot modify anything                        │
│       └─► Ideal for reporting/auditing                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Database Schema

```
┌─────────────────────────────────────┐
│         users TABLE                 │
├─────────────────────────────────────┤
│ id (PK)                 INT         │
│ username (UNIQUE)       VARCHAR     │
│ email (UNIQUE)          VARCHAR     │
│ hashed_password         VARCHAR     │
│ full_name               VARCHAR     │
│ role                    VARCHAR     │
│   - admin               │
│   - editor              │
│   - viewer              │
│ is_active               BOOLEAN     │
│ created_at              DATETIME    │
│ updated_at              DATETIME    │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│         roles TABLE                 │
├─────────────────────────────────────┤
│ id (PK)                 INT         │
│ name                    VARCHAR     │
│ description             TEXT        │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  customer_orders TABLE              │
├─────────────────────────────────────┤
│ id (PK)                 INT         │
│ code                    VARCHAR     │
│ customer_id             INT         │
│ status                  INT         │
│ ... other fields ...    ...         │
└─────────────────────────────────────┘
```

## JWT Token Structure

```
┌──────────────────────────────────────────────────────────┐
│ JWT Token Format                                         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.                   │
│ eyJzdWIiOiJqb2huX2RvZSIsImV4cCI6MTcwNDQwMDAwMH0.        │
│ SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c            │
│                                                          │
│  └─ Header          └─ Payload           └─ Signature   │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ Header (Base64 Decoded):                                 │
│ {                                                        │
│   "alg": "HS256",                                        │
│   "typ": "JWT"                                           │
│ }                                                        │
│                                                          │
│ Payload (Base64 Decoded):                                │
│ {                                                        │
│   "sub": "john_doe",                  (username)         │
│   "exp": 1704400000,                  (expiration time)  │
│   "iat": 1704313600                   (issued at)        │
│ }                                                        │
│                                                          │
│ Signature (HS256):                                       │
│ HMACSHA256(                                              │
│   base64UrlEncode(header) + "." +                        │
│   base64UrlEncode(payload),                              │
│   secret_key                                             │
│ )                                                        │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Security Layers

```
┌──────────────────────────────────────────────────────────┐
│              Security Architecture                       │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ Layer 1: HTTPS/TLS (Transport)                           │
│ └─► Encrypts tokens in transit                          │
│                                                          │
│ Layer 2: JWT Token (Authentication)                      │
│ └─► Verifies user identity                              │
│     ├─► Signature verification                          │
│     ├─► Expiration check                                │
│     └─► User validation                                 │
│                                                          │
│ Layer 3: RBAC (Authorization)                            │
│ └─► Checks user permissions                             │
│     ├─► Role validation                                 │
│     ├─► Permission matrix                               │
│     └─► Resource access control                         │
│                                                          │
│ Layer 4: Password Security (Storage)                     │
│ └─► Bcrypt hashing                                      │
│     ├─► Automatic salt generation                       │
│     ├─► One-way hashing                                 │
│     └─► Never store plain text                          │
│                                                          │
│ Layer 5: MRPeasy Protection (API)                        │
│ └─► Read-only integration                               │
│     ├─► No write operations                             │
│     ├─► No delete operations                            │
│     └─► One-way sync only                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Complete Request Example

```
Client Browser                FastAPI Backend              Database
│                            │                            │
├─ POST /api/auth/login  ──► │                            │
│ {                          │                            │
│   username: "john_doe"     │                            │
│   password: "password123"  │                            │
│ }                          │                            │
│                            │ SELECT FROM users    ──►   │
│                            │ WHERE username='john_doe'  │
│                            │ ◄──────────────────────    │
│                            │ User found, role='viewer'   │
│                            │ Verify bcrypt hash      ✅  │
│                            │ Generate JWT token         │
│ ◄── {token, user} ────────│                            │
│ Store token in localStorage│                            │
│                            │                            │
├─ GET /customer-orders/ ──► │                            │
│ Authorization: Bearer ...  │ Decode JWT                 │
│                            │ Check expiration        ✅  │
│                            │ Get current user        ✅  │
│                            │ Check role: viewer          │
│                            │ viewer.read = true      ✅  │
│                            │ SELECT FROM orders   ──►    │
│                            │ ◄──────────────────────    │
│ ◄── [orders list] ────────│ Return to client           │
│ Display orders            │                            │
│                            │                            │
├─ PUT /customer-orders/1 ► │                            │
│ Authorization: Bearer ...  │ Decode JWT                 │
│ {status: 2}               │ Check expiration        ✅  │
│                            │ Get current user        ✅  │
│                            │ Check role: viewer          │
│                            │ viewer.write = false    ❌  │
│ ◄── 403 Forbidden ────────│ Return error                │
│ Show error message        │                            │
│                            │                            │
```

This architecture ensures:
✅ Secure authentication with JWT tokens
✅ Role-based authorization
✅ Bcrypt password security
✅ Protected data endpoints
✅ MRPeasy API protection (read-only)
✅ Clear permission separation
