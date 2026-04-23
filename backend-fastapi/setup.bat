@echo off
echo Installing MRPeasy FastAPI Backend...
echo.

echo Creating virtual environment...
python -m venv venv

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Installation complete!
echo.
echo Next steps:
echo 1. Configure MRPeasy API credentials:
echo    - Copy .env.example to .env
echo    - Add your MRPeasy API key and secret
echo.
echo 2. Activate virtual environment:
echo    venv\Scripts\activate.bat
echo.
echo 3. Run FastAPI server:
echo    python -m uvicorn app.main:app --reload
echo.
echo Backend will run on http://localhost:8000
echo Swagger UI: http://localhost:8000/docs
echo.
pause
