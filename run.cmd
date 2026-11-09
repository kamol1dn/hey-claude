@echo off
rem Start hey-claude without a console window hanging around: use run.cmd from
rem a terminal to see logs, or pythonw for a silent start (see README).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m heyclaude %*
