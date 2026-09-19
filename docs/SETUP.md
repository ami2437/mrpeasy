# Backend Setup

## Python environment

```powershell
cd c:\mrpeasy\backend-fastapi
.\mrpeasy\Scripts\Activate.ps1      # PowerShell
mrpeasy\Scripts\activate.bat        # cmd
source mrpeasy/Scripts/activate     # bash
```

Python 3.13.2 virtual environment at `backend-fastapi/mrpeasy`.

Key packages: FastAPI, SQLAlchemy 2.x, Uvicorn, Pydantic 2.x, python-jose
(JWT), passlib + bcrypt (password hashing), python-dotenv, requests. Full
pinned list in `backend-fastapi/requirements.txt`.

## Configure credentials

```bash
cp .env.example .env
# edit .env with MRPeasy API credentials
```

## Run

```powershell
cd c:\mrpeasy\backend-fastapi
.\mrpeasy\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

## Verify installation

```powershell
pip list
pip show fastapi
python -c "import fastapi, sqlalchemy, passlib; print('OK')"
```

Direct interpreter path if needed:
`C:\mrpeasy\backend-fastapi\mrpeasy\Scripts\python.exe`

## Frontend

See [frontend/README.md](../frontend/README.md) for the React app, and
`frontend/public/*.html` for the static label/packing-slip tools.

## Deactivate

```powershell
deactivate
```
