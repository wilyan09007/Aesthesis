@echo off
setlocal

REM dev.cmd — Windows entry point for the Aesthesis dev stack.
REM
REM cmd.exe / PowerShell can't execute dev.sh directly. This wrapper
REM finds Git for Windows' bash.exe and hands the script off to it.
REM
REM We intentionally do NOT fall back to WSL bash: dev.sh assumes the
REM Windows-side python, npm, and taskkill.exe binaries on PATH, which
REM WSL bash can't see. Git for Windows' msys bash has the right view.

set "DEV_SH=%~dp0dev.sh"
set "BASH_EXE="

if exist "%ProgramFiles%\Git\bin\bash.exe"      set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LocalAppData%\Programs\Git\bin\bash.exe"

if not defined BASH_EXE (
    echo [dev] ERROR: Git Bash not found.>&2
    echo [dev]        Install Git for Windows from https://git-scm.com/ and re-run.>&2
    echo [dev]        ^(WSL bash is not used — dev.sh expects Windows-side python/npm.^)>&2
    exit /b 1
)

"%BASH_EXE%" "%DEV_SH%" %*
exit /b %errorlevel%
