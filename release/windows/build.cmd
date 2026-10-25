@echo off
rem Thin wrapper so `release\windows\build.cmd [-Zip]` works from cmd.exe.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
