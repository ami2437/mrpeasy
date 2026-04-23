# RBAC Quick Start Guide

## 1. Installation

Ensure all auth dependencies are installed:

```bash
pip install -r requirements.txt
```

Key packages:
- `python-jose[cryptography]==3.3.0` - JWT token handling
- `passlib[bcrypt]==1.7.4` - Password hashing
- `python-multipart==0.0.6` - Form data parsing

## 2. Database Setup

Auth tables are automatically created on first run:

```bash
# Tables created:
# - users (username, email, hashed_password, role, is_active, timestamps)
# - roles (role definitions)
```

## 3. Create First Admin User

Start your server and create the initial admin user:

```bash
# Register admin user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "email": "admin@mrpeasy.local",
    "password": "AdminPassword123!",
    "full_name": "Administrator",
    "role": "admin"
  }'
```

## 4. Login and Get Token

```bash
# Login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "password": "AdminPassword123!"
  }'

# Returns:
# {
#   "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#   "token_type": "bearer",
#   "user": {...}
# }
```

## 5. Test Protected Endpoints

```bash
# Use token from login
TOKEN="your_token_here"

# Test read access (works for all authenticated users)
curl -X GET "http://localhost:8000/customer-orders/" \
  -H "Authorization: Bearer $TOKEN"

# Test admin endpoint (requires admin role)
curl -X GET "http://localhost:8000/api/auth/users" \
  -H "Authorization: Bearer $TOKEN"
```

## 6. User Management

### Create New Users

```bash
# As admin, create users with different roles
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "editor_user",
    "email": "editor@mrpeasy.local",
    "password": "EditorPass123!",
    "full_name": "Editor User",
    "role": "editor"
  }'
```

### List All Users (Admin Only)

```bash
curl -X GET "http://localhost:8000/api/auth/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Update User Role (Admin Only)

```bash
curl -X PUT "http://localhost:8000/api/auth/users/2" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "editor"
  }'
```

## 7. Frontend Integration (React)

### Login Example

```javascript
import axios from 'axios';

async function login(username, password) {
  try {
    const response = await axios.post(
      'http://localhost:8000/api/auth/login',
      { username, password }
    );
    
    const { access_token, user } = response.data;
    
    // Store token in localStorage
    localStorage.setItem('token', access_token);
    localStorage.setItem('user', JSON.stringify(user));
    
    return { success: true, user };
  } catch (error) {
    return { 
      success: false, 
      error: error.response?.data?.detail || 'Login failed' 
    };
  }
}
```

### API Request with Token

```javascript
function getAxiosConfig() {
  const token = localStorage.getItem('token');
  return {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  };
}

// Usage
async function fetchCustomerOrders() {
  try {
    const response = await axios.get(
      'http://localhost:8000/customer-orders/',
      getAxiosConfig()
    );
    return response.data;
  } catch (error) {
    console.error('Error fetching orders:', error);
  }
}
```

### Protected Component (React)

```javascript
import { useEffect, useState } from 'react';

export function AdminPanel() {
  const [users, setUsers] = useState([]);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const user = JSON.parse(localStorage.getItem('user'));
    
    if (user?.role !== 'admin') {
      setIsAdmin(false);
      return;
    }
    
    setIsAdmin(true);
    fetchUsers();
  }, []);

  async function fetchUsers() {
    try {
      const token = localStorage.getItem('token');
      const response = await axios.get(
        'http://localhost:8000/api/auth/users',
        {
          headers: { 'Authorization': `Bearer ${token}` }
        }
      );
      setUsers(response.data);
    } catch (error) {
      console.error('Error fetching users:', error);
    }
  }

  if (!isAdmin) {
    return <div>Access Denied</div>;
  }

  return (
    <div>
      <h1>Users Management</h1>
      <ul>
        {users.map(user => (
          <li key={user.id}>
            {user.username} - {user.role}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

## 8. Environment Configuration

Update `.env` file for production:

```bash
# JWT Configuration
JWT_SECRET_KEY=your-very-secure-random-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Database
DATABASE_URL=sqlite:///./mrpeasy.db
# Or for PostgreSQL:
# DATABASE_URL=postgresql://user:password@localhost/mrpeasy_db

# CORS
CORS_ORIGINS=["http://localhost:3000","https://yourapp.com"]
```

## 9. Common Workflows

### Workflow 1: New User Registration

1. User opens registration page
2. User fills registration form
3. Frontend POST to `/api/auth/register`
4. Backend creates user in database
5. User can now login

### Workflow 2: Editor Creating Orders

1. Editor logs in → gets JWT token
2. Editor views orders → GET `/customer-orders/`
3. Editor modifies order → PUT `/customer-orders/{id}`
4. Editor syncs data → POST `/sync/all` (requires editor role)
5. System logs the changes

### Workflow 3: Admin User Management

1. Admin logs in → gets admin token
2. Admin views users → GET `/api/auth/users`
3. Admin creates new user → POST `/api/auth/register`
4. Admin changes user role → PUT `/api/auth/users/{id}`
5. Admin removes inactive user → DELETE `/api/auth/users/{id}`

## 10. Testing with Swagger UI

Access the interactive API documentation:

```
http://localhost:8000/docs
```

Features:
- Try endpoints directly in browser
- See request/response examples
- Automatic token management in Swagger UI

Steps:
1. Go to `/docs`
2. Click "Authorize" button
3. Login to get token
4. Try auth endpoints directly
5. Test protected endpoints with token

## Troubleshooting

### "Invalid authentication credentials"
- Check token is not expired
- Verify token format: `Bearer <token>`
- Try logging in again

### "Access denied. Required permission: write"
- User role must be admin or editor
- Contact admin to upgrade role

### "User not found"
- Verify username is correct
- Check user hasn't been deleted
- Try registering new user

## Security Reminders

✅ DO:
- Change JWT secret key in production
- Use HTTPS for all production requests
- Regularly update user roles
- Keep tokens in secure storage (HttpOnly cookies)

❌ DON'T:
- Expose secret key in code
- Send tokens in URLs
- Store tokens in localStorage on sensitive apps
- Allow token reuse after logout
- Use weak passwords

## Next Steps

1. Deploy to production with secure JWT key
2. Set up HTTPS/SSL certificates
3. Implement token refresh mechanism
4. Add user activity logging
5. Consider 2FA for admin accounts
