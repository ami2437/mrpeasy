# Role-Based Access Control (RBAC) Documentation

## Overview

The MRPeasy Custom Portal implements a robust role-based access control system using JWT authentication and role-based permissions. This ensures secure access to the API and protects sensitive manufacturing data.

## Authentication System

### JWT (JSON Web Tokens)

The API uses JWT for stateless authentication. Each user receives a token upon successful login that grants access to protected endpoints.

**Token Structure:**
- Algorithm: HS256
- Expiration: 24 hours (default) or configurable via settings
- Includes: Username and expiration time

**Usage:**
Include the token in the Authorization header:
```
Authorization: Bearer <your_jwt_token>
```

### Password Security

Passwords are securely hashed using bcrypt with salt rounds:
- Algorithm: bcrypt
- Salting: Automatic (passlib handles it)
- Never stored as plain text

## User Roles

The system defines three role tiers with increasing levels of access:

### 1. **Admin** - Full Access
- **Read**: ✅ All data
- **Write**: ✅ Modify all local data
- **Delete**: ✅ Delete local data
- **Sync**: ✅ Sync MRPeasy data
- **Manage Users**: ✅ Create, update, delete users
- **Manage Roles**: ✅ Assign roles to users

**Typical Use Cases:**
- System administrators
- Portal managers
- Data governance roles

### 2. **Editor** - Read + Write Access
- **Read**: ✅ All data
- **Write**: ✅ Modify local data
- **Delete**: ❌ Cannot delete
- **Sync**: ✅ Sync MRPeasy data
- **Manage Users**: ❌ Cannot manage users
- **Manage Roles**: ❌ Cannot manage roles

**Typical Use Cases:**
- Production supervisors
- Order managers
- Inventory coordinators
- Data entry staff

### 3. **Viewer** - Read-Only Access
- **Read**: ✅ View all data
- **Write**: ❌ Cannot modify
- **Delete**: ❌ Cannot delete
- **Sync**: ❌ Cannot sync
- **Manage Users**: ❌ Cannot manage users
- **Manage Roles**: ❌ Cannot manage roles

**Typical Use Cases:**
- Quality assurance staff
- Sales team
- Management/reporting
- Consultants/auditors

## Permission Matrix

| Permission | Admin | Editor | Viewer |
|-----------|-------|--------|--------|
| Read Data | ✅ | ✅ | ✅ |
| Modify Data | ✅ | ✅ | ❌ |
| Delete Data | ✅ | ❌ | ❌ |
| Sync Data | ✅ | ✅ | ❌ |
| Create User | ✅ | ❌ | ❌ |
| Update User | ✅ | ❌ | ❌ |
| Delete User | ✅ | ❌ | ❌ |

## API Endpoints

### Authentication Endpoints

#### 1. Register a New User
```
POST /api/auth/register
```

**Request Body:**
```json
{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "secure_password",
  "full_name": "John Doe",
  "role": "viewer"
}
```

**Response:**
```json
{
  "id": 1,
  "username": "john_doe",
  "email": "john@example.com",
  "full_name": "John Doe",
  "role": "viewer",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00"
}
```

**Status Codes:**
- 200: User created successfully
- 400: Username or email already exists

---

#### 2. Login
```
POST /api/auth/login
```

**Request Body:**
```json
{
  "username": "john_doe",
  "password": "secure_password"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "john_doe",
    "email": "john@example.com",
    "full_name": "John Doe",
    "role": "viewer",
    "is_active": true,
    "created_at": "2024-01-15T10:30:00"
  }
}
```

**Status Codes:**
- 200: Login successful
- 401: Invalid username or password
- 403: User is inactive

---

#### 3. Get Current User
```
GET /api/auth/me
Authorization: Bearer <token>
```

**Response:**
```json
{
  "id": 1,
  "username": "john_doe",
  "email": "john@example.com",
  "full_name": "John Doe",
  "role": "viewer",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00"
}
```

**Status Codes:**
- 200: Success
- 401: Invalid or missing token

---

#### 4. Update Current User
```
PUT /api/auth/me
Authorization: Bearer <token>
```

**Request Body (all optional):**
```json
{
  "email": "newemail@example.com",
  "full_name": "John Updated Doe",
  "role": "editor"
}
```

**Note:** Regular users cannot change their own role. Only admins can change roles.

**Status Codes:**
- 200: Updated successfully
- 400: Email already in use
- 403: Not authorized to change role

---

#### 5. List All Users (Admin Only)
```
GET /api/auth/users
Authorization: Bearer <admin_token>
```

**Response:**
```json
[
  {
    "id": 1,
    "username": "john_doe",
    "email": "john@example.com",
    "full_name": "John Doe",
    "role": "viewer",
    "is_active": true,
    "created_at": "2024-01-15T10:30:00"
  },
  {
    "id": 2,
    "username": "jane_admin",
    "email": "jane@example.com",
    "full_name": "Jane Admin",
    "role": "admin",
    "is_active": true,
    "created_at": "2024-01-16T14:20:00"
  }
]
```

**Status Codes:**
- 200: Success
- 401: Invalid token
- 403: User is not admin

---

#### 6. Get Specific User (Admin or Own Profile)
```
GET /api/auth/users/{user_id}
Authorization: Bearer <token>
```

**Status Codes:**
- 200: Success
- 401: Invalid token
- 403: Not authorized (can only view own profile unless admin)
- 404: User not found

---

#### 7. Update User (Admin Only)
```
PUT /api/auth/users/{user_id}
Authorization: Bearer <admin_token>
```

**Request Body (all optional):**
```json
{
  "email": "newemail@example.com",
  "full_name": "Updated Name",
  "role": "editor"
}
```

**Status Codes:**
- 200: Updated successfully
- 400: Email already in use
- 401: Invalid token
- 403: Not admin
- 404: User not found

---

#### 8. Delete User (Admin Only)
```
DELETE /api/auth/users/{user_id}
Authorization: Bearer <admin_token>
```

**Status Codes:**
- 200: Deleted successfully
- 400: Cannot delete own account
- 401: Invalid token
- 403: Not admin
- 404: User not found

---

### Protected Data Endpoints

All data endpoints now require authentication and check user permissions:

#### Customer Orders
```
GET    /customer-orders/                [Read] - All authenticated users
GET    /customer-orders/{order_id}      [Read] - All authenticated users
PUT    /customer-orders/{order_id}      [Write] - Admin, Editor only
DELETE /customer-orders/{order_id}      [Delete] - Admin only
```

#### Sync Operations
```
POST /sync/customer-orders      [Sync] - Admin, Editor only
POST /sync/stock-items          [Sync] - Admin, Editor only
POST /sync/manufacturing-orders [Sync] - Admin, Editor only
POST /sync/all                  [Sync] - Admin, Editor only
```

## Usage Examples

### Example 1: User Registration and Login

```bash
# 1. Register a new user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "email": "john@example.com",
    "password": "secure_password",
    "full_name": "John Doe",
    "role": "viewer"
  }'

# 2. Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "password": "secure_password"
  }'

# Response will include access_token
```

### Example 2: Accessing Protected Data

```bash
# Use the token from login response
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Read customer orders (allowed for all users)
curl -X GET "http://localhost:8000/customer-orders/" \
  -H "Authorization: Bearer $TOKEN"

# Update customer order (requires editor or admin role)
curl -X PUT "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": 2,
    "notes": "Updated notes"
  }'

# This will fail if user is "viewer" role
```

### Example 3: Admin User Management

```bash
# Admin token
ADMIN_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# List all users
curl -X GET "http://localhost:8000/api/auth/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Update user role
curl -X PUT "http://localhost:8000/api/auth/users/1" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "editor"
  }'

# Delete user
curl -X DELETE "http://localhost:8000/api/auth/users/2" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

## Configuration

JWT settings can be configured in `app/config/settings.py`:

```python
# JWT Configuration
secret_key: str = "your-secret-key-change-in-production-12345"
algorithm: str = "HS256"
access_token_expire_minutes: int = 1440  # 24 hours
```

**Important Security Notes:**
1. Change `secret_key` in production - use a strong random string
2. Keep `secret_key` secure - never commit to version control
3. Adjust `access_token_expire_minutes` based on security requirements
4. Use HTTPS in production to protect token transmission

## Error Handling

The API returns standard HTTP status codes with descriptive error messages:

| Status | Meaning | Example |
|--------|---------|---------|
| 200 | Success | Request completed successfully |
| 400 | Bad Request | Invalid request data or user already exists |
| 401 | Unauthorized | Invalid/missing authentication token |
| 403 | Forbidden | User lacks required permissions or role |
| 404 | Not Found | Resource doesn't exist |
| 500 | Server Error | Backend error occurred |

**Error Response Format:**
```json
{
  "detail": "User has insufficient permissions to perform this action"
}
```

## Security Best Practices

1. **Tokens**
   - Store tokens securely (preferably in HTTPOnly cookies)
   - Never expose tokens in URLs
   - Implement token refresh mechanism for long sessions
   - Log out by discarding client-side token

2. **Passwords**
   - Enforce strong password requirements
   - Consider MFA for admin accounts
   - Never transmit passwords in URLs
   - Always use HTTPS

3. **Access Control**
   - Regularly audit user roles and permissions
   - Implement principle of least privilege
   - Remove admin access when not needed
   - Monitor sync operations for data integrity

4. **Database**
   - Encrypt sensitive data at rest
   - Use parameterized queries (SQLAlchemy does this)
   - Regular database backups
   - Implement audit logging for sensitive operations

## Troubleshooting

### "Invalid authentication credentials"
- Token may be expired (> 24 hours old)
- Token may be malformed
- Try logging in again to get a fresh token

### "Access denied. Required roles: admin"
- User's role doesn't grant permission
- Contact admin to update role
- Verify token belongs to correct user

### "User not found"
- User account may have been deleted
- Check if user was deactivated
- Verify correct username in login

## Future Enhancements

Planned improvements to RBAC system:
1. Token refresh endpoint for longer sessions
2. Role-based API key authentication
3. Two-factor authentication (2FA) for admin accounts
4. Fine-grained permission system with resource-level access
5. Audit logging for all API access
6. LDAP/Active Directory integration for enterprise deployments
