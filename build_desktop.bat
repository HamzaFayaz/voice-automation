@echo off
setlocal

cd /d "%~dp0"
pyinstaller voice_automation_desktop.spec
