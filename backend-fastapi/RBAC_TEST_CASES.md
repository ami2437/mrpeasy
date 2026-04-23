# RBAC Test Cases and Examples

## Test Cases by Role

### Test Case 1: Viewer Role - Read Access ✅

**Scenario:** Viewer user can read data but cannot modify

```bash
# 1. Register viewer user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer_user",
    "email": "viewer@test.local",
    "password": "Password123!",
    "role": "viewer"
  }'
# Expected: 200, user created with role "viewer"

# 2. Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer_user",
    "password": "Password123!"
  }'
# Expected: 200, JWT token in response
# STORE TOKEN: export VIEWER_TOKEN="..."

# 3. Get current user info
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 200, shows viewer user info

# 4. Try to read orders (should work)
curl -X GET "http://localhost:8000/customer-orders/" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 200, returns list of orders

# 5. Try to modify order (should fail)
curl -X PUT "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": 2}'
# Expected: 403 Forbidden, "Access denied. Required permission: write"

# 6. Try to sync (should fail)
curl -X POST "http://localhost:8000/sync/customer-orders" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 403 Forbidden, "Access denied. Required permission: sync"

# 7. Try to delete (should fail)
curl -X DELETE "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 403 Forbidden, "Access denied. Required permission: delete"
```

**Expected Results:**
- ✅ Can register as viewer
- ✅ Can login and get token
- ✅ Can view orders
- ❌ Cannot modify orders
- ❌ Cannot sync data
- ❌ Cannot delete orders

---

### Test Case 2: Editor Role - Read + Write + Sync ✅

**Scenario:** Editor can read, write, and sync but cannot delete

```bash
# 1. Register editor user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "editor_user",
    "email": "editor@test.local",
    "password": "Password123!",
    "role": "editor"
  }'
# Expected: 200

# 2. Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "editor_user",
    "password": "Password123!"
  }'
# Expected: 200, get JWT token
# STORE TOKEN: export EDITOR_TOKEN="..."

# 3. Read orders (should work)
curl -X GET "http://localhost:8000/customer-orders/" \
  -H "Authorization: Bearer $EDITOR_TOKEN"
# Expected: 200

# 4. Modify order (should work)
curl -X PUT "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $EDITOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": 2, "notes": "Updated by editor"}'
# Expected: 200, order updated

# 5. Sync data (should work)
curl -X POST "http://localhost:8000/sync/customer-orders" \
  -H "Authorization: Bearer $EDITOR_TOKEN"
# Expected: 200, sync completed

# 6. Try to delete (should fail)
curl -X DELETE "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $EDITOR_TOKEN"
# Expected: 403 Forbidden, "Access denied. Required permission: delete"

# 7. Try to manage users (should fail)
curl -X GET "http://localhost:8000/api/auth/users" \
  -H "Authorization: Bearer $EDITOR_TOKEN"
# Expected: 403 Forbidden, "Access denied. Required roles: admin"
```

**Expected Results:**
- ✅ Can read orders
- ✅ Can modify orders
- ✅ Can sync data
- ❌ Cannot delete orders
- ❌ Cannot manage users

---

### Test Case 3: Admin Role - Full Access ✅

**Scenario:** Admin has full access to all operations

```bash
# 1. Register admin user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin_user",
    "email": "admin@test.local",
    "password": "AdminPassword123!",
    "role": "admin"
  }'
# Expected: 200

# 2. Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin_user",
    "password": "AdminPassword123!"
  }'
# Expected: 200
# STORE TOKEN: export ADMIN_TOKEN="..."

# 3. Read orders (should work)
curl -X GET "http://localhost:8000/customer-orders/" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 200

# 4. Modify order (should work)
curl -X PUT "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": 3}'
# Expected: 200

# 5. Delete order (should work)
curl -X DELETE "http://localhost:8000/customer-orders/1" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 204 No Content

# 6. Sync data (should work)
curl -X POST "http://localhost:8000/sync/all" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 200

# 7. Manage users - List all users (should work)
curl -X GET "http://localhost:8000/api/auth/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 200, list of all users

# 8. Manage users - Create user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "new_user",
    "email": "new@test.local",
    "password": "NewPass123!",
    "role": "editor"
  }'
# Expected: 200

# 9. Manage users - Update user role
curl -X PUT "http://localhost:8000/api/auth/users/2" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "admin"
  }'
# Expected: 200

# 10. Manage users - Delete user
curl -X DELETE "http://localhost:8000/api/auth/users/3" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# Expected: 200
```

**Expected Results:**
- ✅ Full access to all operations
- ✅ Can read, modify, delete data
- ✅ Can sync all data
- ✅ Can manage users

---

## Authentication Error Test Cases

### Test Case 4: Invalid Token ❌

**Scenario:** Sending invalid token should return 401

```bash
# 1. Try to access protected endpoint without token
curl -X GET "http://localhost:8000/api/auth/me"
# Expected: 403 Forbidden or 401 Unauthorized (depends on implementation)

# 2. Try with malformed token
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer invalid_token_12345"
# Expected: 401 Unauthorized, "Invalid authentication credentials"

# 3. Try with expired token
# (Create a token, wait for it to expire, or manually set exp to past time)
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2MDAwMDAwMDB9.xxx"
# Expected: 401 Unauthorized

# 4. Try with wrong secret key
# (Token signed with different secret)
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer wrong_secret_token"
# Expected: 401 Unauthorized, "Invalid authentication credentials"
```

**Expected Results:**
- ✅ Missing token returns error
- ✅ Invalid token returns 401
- ✅ Expired token returns 401
- ✅ Tampered token returns 401

---

### Test Case 5: Invalid Credentials ❌

**Scenario:** Wrong username/password should fail login

```bash
# 1. Login with wrong password
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer_user",
    "password": "wrong_password"
  }'
# Expected: 401 Unauthorized, "Invalid username or password"

# 2. Login with non-existent user
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "nonexistent_user",
    "password": "Password123!"
  }'
# Expected: 401 Unauthorized, "Invalid username or password"

# 3. Login with inactive user
# (First create and deactivate user, then try login)
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "inactive_user",
    "password": "Password123!"
  }'
# Expected: 403 Forbidden, "User is not active"
```

**Expected Results:**
- ✅ Wrong password rejected
- ✅ Non-existent user rejected
- ✅ Inactive user rejected

---

## Registration Validation Test Cases

### Test Case 6: Duplicate User ❌

**Scenario:** Cannot register with existing username or email

```bash
# 1. Register first user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com",
    "password": "Password123!"
  }'
# Expected: 200, user created

# 2. Try to register with same username
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "different@example.com",
    "password": "Password123!"
  }'
# Expected: 400 Bad Request, "Username already registered"

# 3. Try to register with same email
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "differentuser",
    "email": "test@example.com",
    "password": "Password123!"
  }'
# Expected: 400 Bad Request, "Email already registered"
```

**Expected Results:**
- ✅ Duplicate username prevented
- ✅ Duplicate email prevented

---

## Permission Escalation Prevention Test Cases

### Test Case 7: Viewer Cannot Escalate Role ❌

**Scenario:** Viewer cannot change own role to admin

```bash
# 1. Login as viewer
VIEWER_TOKEN="..."

# 2. Try to change own role (should fail)
curl -X PUT "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "admin"
  }'
# Expected: 403 Forbidden, "Only admins can change user roles"

# 3. Check role is still viewer
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $VIEWER_TOKEN"
# Expected: 200, role is still "viewer"
```

**Expected Results:**
- ✅ Viewer cannot change own role
- ✅ Only admins can change roles

---

## Password Security Test Cases

### Test Case 8: Password Hashing ✅

**Scenario:** Passwords are securely hashed with bcrypt

```bash
# 1. Register user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "secure_test",
    "email": "secure@test.local",
    "password": "MySecurePassword123!"
  }'
# Expected: 200

# 2. Check database (should NOT contain plain password)
# SELECT hashed_password FROM users WHERE username='secure_test'
# Expected: $2b$12$... (bcrypt hash, NOT "MySecurePassword123!")

# 3. Verify password works for login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "secure_test",
    "password": "MySecurePassword123!"
  }'
# Expected: 200, login successful

# 4. Try login with similar but wrong password
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "secure_test",
    "password": "MySecurePassword123"  # Missing !
  }'
# Expected: 401, wrong password
```

**Expected Results:**
- ✅ Password stored as bcrypt hash
- ✅ Plain password not in database
- ✅ Correct password works
- ✅ Similar passwords fail

---

## Token Expiration Test Cases

### Test Case 9: Token Expiration ⏱️

**Scenario:** Tokens expire after configured time

```bash
# 1. Login and get token
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test_user",
    "password": "Password123!"
  }'
# Expected: 200, token with exp claim

# 2. Decode token to see expiration
# Use jwt.io or similar to decode
# Check exp field (should be ~24 hours from now by default)

# 3. Use token immediately (should work)
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $TOKEN"
# Expected: 200

# 4. Wait for token to expire or set exp to past time
# Then try to use expired token
curl -X GET "http://localhost:8000/api/auth/me" \
  -H "Authorization: Bearer $EXPIRED_TOKEN"
# Expected: 401, "Invalid authentication credentials"

# 5. Login again to get fresh token
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "test_user",
    "password": "Password123!"
  }'
# Expected: 200, new token
```

**Expected Results:**
- ✅ Token contains expiration time
- ✅ Fresh token works
- ✅ Expired token fails
- ✅ Can get new token by logging in again

---

## Integration Test Example

### Complete User Journey

```bash
#!/bin/bash

BASE_URL="http://localhost:8000"

# Step 1: Register as viewer
echo "1. Registering viewer user..."
VIEWER_RESPONSE=$(curl -s -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer_journey",
    "email": "viewer_journey@test.local",
    "password": "ViewerPass123!",
    "role": "viewer"
  }')
echo $VIEWER_RESPONSE | jq '.'

# Step 2: Login as viewer
echo "2. Logging in as viewer..."
VIEWER_LOGIN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer_journey",
    "password": "ViewerPass123!"
  }')
VIEWER_TOKEN=$(echo $VIEWER_LOGIN | jq -r '.access_token')
echo "Viewer Token: $VIEWER_TOKEN"

# Step 3: View orders
echo "3. Viewing orders as viewer..."
curl -s -X GET "$BASE_URL/customer-orders/" \
  -H "Authorization: Bearer $VIEWER_TOKEN" | jq '.[] | {id, code, status}'

# Step 4: Try to modify (expect failure)
echo "4. Trying to modify order as viewer (should fail)..."
curl -s -X PUT "$BASE_URL/customer-orders/1" \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": 2}' | jq '.'

# Step 5: Admin upgrade
echo "5. Admin user upgrading viewer to editor..."
ADMIN_LOGIN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "AdminPassword123!"}')
ADMIN_TOKEN=$(echo $ADMIN_LOGIN | jq -r '.access_token')

curl -s -X PUT "$BASE_URL/api/auth/users/1" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}' | jq '.'

# Step 6: Login again to get new token
echo "6. Logging in again with upgraded role..."
NEW_LOGIN=$(curl -s -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "viewer_journey", "password": "ViewerPass123!"}')
NEW_TOKEN=$(echo $NEW_LOGIN | jq -r '.access_token')

# Step 7: Try to modify again (should work now)
echo "7. Modifying order as editor (should succeed)..."
curl -s -X PUT "$BASE_URL/customer-orders/1" \
  -H "Authorization: Bearer $NEW_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": 2}' | jq '.'

echo "Journey complete!"
```

---

## Summary of Test Coverage

| Test Category | Test Case | Status |
|---|---|---|
| Read Access | Viewer can read | ✅ |
| Write Access | Viewer cannot write | ✅ |
| Delete Access | Viewer cannot delete | ✅ |
| Sync Access | Viewer cannot sync | ✅ |
| Editor Access | Editor can write/sync | ✅ |
| Editor Limits | Editor cannot delete | ✅ |
| Admin Access | Admin has full access | ✅ |
| Invalid Token | Missing token fails | ✅ |
| Invalid Credentials | Wrong password fails | ✅ |
| Duplicate Users | Cannot register duplicate | ✅ |
| Password Security | Bcrypt hashing works | ✅ |
| Token Expiration | Tokens expire | ✅ |
| Role Escalation | Cannot escalate role | ✅ |

All test cases verify RBAC system works correctly and securely protects against unauthorized access.
