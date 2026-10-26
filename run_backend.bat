@echo off
title Backend Orchestrator (Port 8000)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

