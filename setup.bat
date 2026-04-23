@echo off
echo Installing MRPeasy Custom Portal...
echo.

echo Setting up backend...
cd backend
call npm install
cd ..

echo.
echo Setting up frontend...
cd frontend
call npm install
cd ..

echo.
echo Installation complete!
echo.
echo Next steps:
echo 1. Configure backend API credentials:
echo    - Copy backend\.env.example to backend\.env
echo    - Add your MRPeasy API key and secret
echo.
echo 2. Start backend:
echo    cd backend
echo    npm run dev
echo.
echo 3. Start frontend (in new terminal):
echo    cd frontend
echo    npm start
echo.
echo Backend will run on http://localhost:5000
echo Frontend will run on http://localhost:3000
pause
