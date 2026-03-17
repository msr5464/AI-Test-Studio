#!/bin/bash

# Knowledge-AI RAGAS Evaluation Script
# ====================================

# Set base directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BASE_DIR="$( dirname "$SCRIPT_DIR" )"
cd "$BASE_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧪 Starting RAGAS Evaluation Pipeline...${NC}"

# Check virtual environment
if [ -d "venv" ]; then
    echo -e "${YELLOW}📦 Activating virtual environment...${NC}"
    source venv/bin/activate
else
    echo -e "${YELLOW}⚠️ Virtual environment not found. Please run scripts/install.sh first.${NC}"
    # exit 1
fi

# Install evaluation dependencies
echo -e "${YELLOW}🛠️ Installing evaluation dependencies...${NC}"
python -m pip install -r tests/evaluation/requirements_eval.txt

# Run generation if testset doesn't exist
if [ ! -f "tests/evaluation/testset.csv" ]; then
    echo -e "${YELLOW}🔄 generating synthetic test set...${NC}"
    ./venv/bin/python3 tests/evaluation/generate_testset.py --size 5
else
    echo -e "${GREEN}✅ Found existing testset at tests/evaluation/testset.csv${NC}"
fi

# Run evaluation
echo -e "${BLUE}📊 Running RAG evaluation...${NC}"
./venv/bin/python3 tests/evaluation/evaluate_rag.py --testset tests/evaluation/testset.csv

echo -e "${GREEN}🏁 Evaluation pipeline complete! Check tests/evaluation/reports/ for detailed reports.${NC}"
