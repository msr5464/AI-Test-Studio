# Initialize Storage Directories
# PowerShell script for Windows

Write-Host "Initializing storage directories..." -ForegroundColor Green

$storageDir = "storage"
$documentsDir = "$storageDir\documents"
$chromaDbDir = "$storageDir\chroma_db"
$embeddingCacheDir = "$storageDir\embedding_cache"
$logsDir = "logs"

# Create directories
New-Item -ItemType Directory -Force -Path $documentsDir | Out-Null
New-Item -ItemType Directory -Force -Path $chromaDbDir | Out-Null
New-Item -ItemType Directory -Force -Path $embeddingCacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Write-Host "✅ Storage directories created successfully!" -ForegroundColor Green
Write-Host "   - Documents: $documentsDir" -ForegroundColor Cyan
Write-Host "   - ChromaDB: $chromaDbDir" -ForegroundColor Cyan
Write-Host "   - Embedding Cache: $embeddingCacheDir" -ForegroundColor Cyan
Write-Host "   - Logs: $logsDir" -ForegroundColor Cyan

