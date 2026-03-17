#!/bin/bash
# Initialize Storage Directories
# Bash script for macOS/Linux

echo "Initializing storage directories..."

# Create directories
mkdir -p storage/documents
mkdir -p storage/chroma_db
mkdir -p storage/embedding_cache
mkdir -p logs

echo "✅ Storage directories created successfully!"
echo "   - Documents: storage/documents"
echo "   - ChromaDB: storage/chroma_db"
echo "   - Embedding Cache: storage/embedding_cache"
echo "   - Logs: logs"

