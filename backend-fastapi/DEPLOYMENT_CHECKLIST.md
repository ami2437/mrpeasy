# RBAC Implementation Checklist & Deployment Guide

## ✅ Implementation Completion Checklist

### Core Components
- [x] Authentication Service (`app/services/auth.py`)
- [x] Auth Routes (`app/routes/auth.py`)
- [x] Dependencies/Middleware (`app/dependencies.py`)
- [x] Database Models (User, Role in `app/models/__init__.py`)
- [x] Pydantic Schemas (updated `app/schemas/__init__.py`)
- [x] JWT Configuration (updated `app/config/settings.py`)
- [x] Main app updated (`app/main.py`)

### Route Protection
- [x] Customer orders routes protected
- [x] Sync routes protected
- [x] Stock items routes protected (inherited protection)
- [x] Manufacturing orders routes protected (inherited protection)
- [x] Auth management routes created

### Documentation
- [x] RBAC_DOCUMENTATION.md (comprehensive)
- [x] RBAC_QUICKSTART.md (quick reference)
- [x] RBAC_ARCHITECTURE.md (diagrams and flows)
- [x] RBAC_TEST_CASES.md (test scenarios)
- [x] RBAC_IMPLEMENTATION.md (implementation details)
- [x] RBAC_README.md (overview summary)

### Security Features
- [x] JWT token generation
- [x] JWT token validation
- [x] Bcrypt password hashing
- [x] Role-based permissions
- [x] Permission decorators
- [x] User activation/deactivation
- [x] Token expiration (24 hours)
- [x] MRPeasy read-only protection maintained

---

## 📋 Pre-Deployment Checklist

### Security Configuration
- [ ] Change `secret_key` in `app/config/settings.py` to strong random value (32+ chars)
- [ ] Update CORS origins to production domain
- [ ] Set up environment variables in `.env` file
- [ ] Enable HTTPS/SSL certificates
- [ ] Review all password policies
- [ ] Set up rate limiting on auth endpoints (optional)

### Database
- [ ] Backup database
- [ ] Test migration scripts (if using)
- [ ] Verify database connection
- [ ] Create initial admin user
- [ ] Test database permissions
- [ ] Set up automated backups

### Testing
- [ ] Test user registration
- [ ] Test user login
- [ ] Test token generation
- [ ] Test protected endpoints with different roles
- [ ] Test permission enforcement
- [ ] Test token expiration
- [ ] Test invalid credentials handling
- [ ] Load testing (optional)

### Documentation
- [ ] Update API documentation
- [ ] Create user guides
- [ ] Document role assignments
- [ ] Create admin procedures manual
- [ ] Document troubleshooting steps
- [ ] Create deployment guide

### Operations
- [ ] Set up monitoring
- [ ] Configure logging
- [ ] Set up error alerts
- [ ] Create backup schedule
- [ ] Document recovery procedures
- [ ] Train support team

---

## 🚀 Deployment Steps

### Step 1: Prepare Environment

```bash
# Clone/pull latest code
git pull origin main

# Create virtual environment
python -m venv venv
source venv/Scripts/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Secrets

Create `.env` file with production values:

```bash
# JWT Configuration - USE STRONG RANDOM KEY!
JWT_SECRET_KEY=your-very-secure-random-key-here-min-32-chars
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# Database
DATABASE_URL=sqlite:///./mrpeasy.db
# Or PostgreSQL: postgresql://user:pass@host/dbname

# CORS
CORS_ORIGINS=["https://yourdomain.com"]

# MRPeasy API
MRPEASY_API_URL=https://api.mrpeasy.com
MRPEASY_API_KEY=your_api_key
MRPEASY_API_SECRET=your_api_secret
```

### Step 3: Initialize Database

```bash
# Start server (creates tables automatically)
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or in another terminal, create initial admin:
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "email": "admin@company.com",
    "password": "SetStrongPasswordHere123!",
    "role": "admin"
  }'
```

### Step 4: Verify Installation

```bash
# Check health endpoint
curl http://localhost:8000/health

# Check API documentation
# Open browser to http://localhost:8000/docs

# Test login
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "SetStrongPasswordHere123!"}'
```

### Step 5: Set Up Production Server

**Option A: Using Gunicorn**

```bash
# Install Gunicorn
pip install gunicorn

# Run with Gunicorn
gunicorn app.main:app -w 4 -b 0.0.0.0:8000 --timeout 60
```

**Option B: Using Docker**

Create `Dockerfile`:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t mrpeasy-portal .
docker run -p 8000:8000 -e JWT_SECRET_KEY="your-secret" mrpeasy-portal
```

**Option C: Using Systemd Service**

Create `/etc/systemd/system/mrpeasy.service`:

```ini
[Unit]
Description=MRPeasy Portal API
After=network.target

[Service]
Type=notify
User=www-data
WorkingDirectory=/opt/mrpeasy
Environment="PATH=/opt/mrpeasy/venv/bin"
ExecStart=/opt/mrpeasy/venv/bin/gunicorn app.main:app -w 4 -b 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable mrpeasy
sudo systemctl start mrpeasy
```

### Step 6: Set Up Reverse Proxy (Nginx)

Create `/etc/nginx/sites-available/mrpeasy`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and restart Nginx:

```bash
sudo ln -s /etc/nginx/sites-available/mrpeasy /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Step 7: Set Up SSL/HTTPS

Using Let's Encrypt with Certbot:

```bash
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

### Step 8: Create Initial Users

```bash
# Admin user (already created)
# Create editor user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "editor",
    "email": "editor@company.com",
    "password": "EditorPassword123!",
    "role": "editor"
  }'

# Create viewer user
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "viewer",
    "email": "viewer@company.com",
    "password": "ViewerPassword123!",
    "role": "viewer"
  }'
```

### Step 9: Set Up Monitoring

Install monitoring tools:

```bash
# Install monitoring packages
pip install prometheus-client
pip install python-json-logger
```

Add to `app/main.py` for metrics:

```python
from prometheus_client import Counter, Histogram

auth_attempts = Counter('auth_attempts_total', 'Total auth attempts', ['status'])
request_duration = Histogram('request_duration_seconds', 'Request duration')
```

### Step 10: Set Up Logging

Configure logging in `app/config/logging.py`:

```python
import logging
from pythonjsonlogger import jsonlogger

# JSON logging for production
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
logHandler.setFormatter(formatter)
logger = logging.getLogger()
logger.addHandler(logHandler)
logger.setLevel(logging.INFO)
```

---

## 🔄 Post-Deployment Checklist

- [ ] Verify all endpoints are accessible
- [ ] Test authentication workflow
- [ ] Verify role-based access
- [ ] Check database backups working
- [ ] Monitor error logs
- [ ] Test user creation and management
- [ ] Verify sync operations
- [ ] Test MRPeasy integration
- [ ] Monitor performance metrics
- [ ] Document any issues

---

## 🛡️ Security Hardening After Deployment

### Essential Security Steps

1. **Disable Default Credentials**
   - [ ] Delete any test users
   - [ ] Change default admin password
   - [ ] Remove development accounts

2. **Enable Logging**
   - [ ] Log all authentication attempts
   - [ ] Log all permission denials
   - [ ] Log data modifications
   - [ ] Monitor for suspicious activity

3. **Set Up Rate Limiting**
   - [ ] Limit login attempts
   - [ ] Limit registration attempts
   - [ ] Prevent brute force attacks

4. **Regular Backups**
   - [ ] Daily database backups
   - [ ] Store backups securely
   - [ ] Test backup restoration

5. **Security Updates**
   - [ ] Keep dependencies updated
   - [ ] Monitor security advisories
   - [ ] Apply patches promptly

6. **Access Control**
   - [ ] Review user roles regularly
   - [ ] Remove inactive users
   - [ ] Enforce strong passwords
   - [ ] Consider password expiration

---

## 📊 Monitoring & Maintenance

### Key Metrics to Monitor

```
- Authentication success/failure rate
- Average response time
- Database connection pool usage
- Token expiration events
- Failed permission checks
- API error rates
- Sync operation duration
- User activity patterns
```

### Regular Maintenance Tasks

- **Weekly**
  - [ ] Review error logs
  - [ ] Check database size
  - [ ] Verify backups

- **Monthly**
  - [ ] Audit user accounts
  - [ ] Review access patterns
  - [ ] Update documentation

- **Quarterly**
  - [ ] Security audit
  - [ ] Performance analysis
  - [ ] Update dependencies

---

## 🚨 Troubleshooting Deployment

### Common Issues

**Issue: Tokens not working after deployment**
- Solution: Verify JWT_SECRET_KEY is same across all instances
- Check token expiration time setting
- Ensure system time is synchronized

**Issue: Database connection errors**
- Solution: Verify DATABASE_URL is correct
- Check database credentials
- Ensure database service is running

**Issue: Slow authentication**
- Solution: Check network latency
- Monitor bcrypt hashing time
- Consider connection pooling

**Issue: Users locked out**
- Solution: Create admin account via direct database insert
- Or restart service with initialization flag

---

## 📞 Emergency Procedures

### If System is Down

1. Check service status
   ```bash
   systemctl status mrpeasy
   ```

2. Check logs
   ```bash
   journalctl -u mrpeasy -n 50
   ```

3. Restart service
   ```bash
   systemctl restart mrpeasy
   ```

4. If database is corrupted
   ```bash
   # Restore from backup
   cp /backups/mrpeasy_latest.db /app/mrpeasy.db
   systemctl restart mrpeasy
   ```

### If Users Cannot Login

1. Check database connectivity
2. Verify user records exist
3. Check authentication service logs
4. Restart authentication service if needed
5. Manually add user via database if critical

---

## 🎓 Documentation for Users

Create user guides:

**Admin Guide**
- User management procedures
- Role assignment
- Troubleshooting
- Backup procedures

**Editor Guide**
- How to use portal
- Data modification procedures
- Sync operations
- Common tasks

**Viewer Guide**
- How to view data
- Report generation
- How to request access changes
- Support contact

---

## ✅ Final Verification

Before considering deployment complete:

- [ ] All endpoints responding correctly
- [ ] Authentication working for all roles
- [ ] Permissions enforced properly
- [ ] Database backups working
- [ ] Monitoring collecting data
- [ ] Logging capturing events
- [ ] Documentation is accurate
- [ ] Users can perform their tasks
- [ ] No security warnings
- [ ] Performance meets requirements

---

## 📞 Support

For issues or questions:

1. Check documentation files
2. Review test cases for examples
3. Check application logs
4. Verify configuration settings
5. Contact development team

**Status: Ready for deployment** ✅
