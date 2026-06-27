@echo off
rem Optional: copy this file to .env.bat and fill in your key if you want AI-written commentary.
rem Without a key, the report still works and produces a local rules-based summary.

set MIMO_API_BASE=https://api.xiaomimimo.com/v1
set MIMO_MODEL=mimo-v2.5-pro
set MIMO_API_KEY=

rem Optional OpenAI fallback.
set OPENAI_API_KEY=
set OPENAI_MODEL=gpt-4.1-mini

rem Optional proxy. Example:
rem set HTTPS_PROXY=http://127.0.0.1:7890
rem set HTTP_PROXY=http://127.0.0.1:7890
