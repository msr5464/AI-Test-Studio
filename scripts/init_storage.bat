@echo off
REM Initialize Storage Directories
REM Batch script for Windows

echo Initializing storage directories...

if not exist storage mkdir storage
if not exist storage\documents mkdir storage\documents
if not exist storage\chroma_db mkdir storage\chroma_db
if not exist storage\embedding_cache mkdir storage\embedding_cache
if not exist logs mkdir logs

echo Storage directories created successfully!
echo   - Documents: storage\documents
echo   - ChromaDB: storage\chroma_db
echo   - Embedding Cache: storage\embedding_cache
echo   - Logs: logs

