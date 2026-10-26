@echo off
title TTS Microservice (Port 8002)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
uvicorn tts_service.app:app --host 0.0.0.0 --port 8002 --reload

