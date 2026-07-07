@echo off
cd /d "%~dp0"
echo.
echo   브라우저에서 이 주소를 여세요:  http://localhost:8123/preview.html
echo   (이 창을 닫으면 서버 종료)
echo.
python -m http.server 8123
