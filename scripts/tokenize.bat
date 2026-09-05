@echo off
rem Tokenize all downloaded corpora into uint16 shards.
set ROOT=%~dp0..
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" tokenize_corpus.py --tokenizer D:/hwk-data/tokenizer/hwk_spm.model
pause