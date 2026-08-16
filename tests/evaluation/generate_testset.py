import os
import sys
import argparse
from pathlib import Path
from typing import List
from dotenv import load_dotenv

# Project root (tests/evaluation/generate_testset.py -> parent.parent.parent)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.rag.rag_document_loader import MultiFormatRAG
from backend.rag.rag_settings import get_config

# Import evaluation specific dependencies (assumed to be installed via requirements_eval.txt)
try:
    from ragas.testset.generator import TestsetGenerator
    from ragas.testset.evolutions import simple, reasoning, multi_context
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from langchain_community.chat_models import ChatOllama
    from langchain_huggingface import HuggingFaceEmbeddings
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        GEMINI_AVAILABLE = True
    except ImportError:
        ChatGoogleGenerativeAI = None
        GEMINI_AVAILABLE = False
except ImportError:
    print("❌ RAGAS dependencies not found. Please run: pip install -r tests/evaluation/requirements_eval.txt")
    sys.exit(1)

def generate_test_data(documents_dir: str, output_path: str, test_size: int = 10):
    load_dotenv(_PROJECT_ROOT / 'config' / '.env')
    
    # 1. Load documents using MultiFormatRAG logic
    print(f"📚 Loading documents from {documents_dir}...")
    config = get_config()
    rag = MultiFormatRAG(config=config)
    
    doc_paths = []
    for ext in ['*.pdf', '*.csv', '*.xlsx', '*.docx', '*.txt', '*.md']:
        doc_paths.extend(Path(documents_dir).glob(ext))
    
    if not doc_paths:
        print(f"⚠️ No documents found in {documents_dir}")
        return

    # Use a simpler loader for Ragas TestsetGenerator which expects LangChain documents
    from langchain_community.document_loaders import DirectoryLoader, UnstructuredFileLoader
    loader = DirectoryLoader(documents_dir, glob="**/*.*", loader_cls=UnstructuredFileLoader)
    documents = loader.load()
    
    print(f"📄 Loaded {len(documents)} document sections.")

    # 2. Setup LLMs for generation
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    gemini_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

    if llm_provider == "openai":
        generator_llm = ChatOpenAI(model="gpt-4o")
        critic_llm = ChatOpenAI(model="gpt-4o")
        embeddings = OpenAIEmbeddings()
    elif llm_provider == "gemini" and gemini_key and GEMINI_AVAILABLE:
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        if gemini_model in ("gemini-1.5-flash", "gemini-1.5-pro"):
            gemini_model = "gemini-2.5-flash"
        generator_llm = ChatGoogleGenerativeAI(model=gemini_model, temperature=0, google_api_key=gemini_key)
        critic_llm = ChatGoogleGenerativeAI(model=gemini_model, temperature=0, google_api_key=gemini_key)
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    else:
        # Using Ollama for generation (may be slow)
        generator_llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
        critic_llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    # 3. Generate Testset
    print(f"🧪 Generating synthetic test set (size: {test_size})...")
    generator = TestsetGenerator.from_langchain(
        generator_llm,
        critic_llm,
        embeddings
    )

    # Define evolution distribution
    distributions = {
        simple: 0.5,
        reasoning: 0.25,
        multi_context: 0.25
    }

    testset = generator.generate_with_langchain_docs(documents, test_size, distributions)
    
    # 4. Save testset
    test_df = testset.to_pandas()
    test_df.to_csv(output_path, index=False)
    print(f"✅ Test set saved to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate RAGAS test set")
    parser.add_argument("--docs", type=str, default="storage/documents", help="Directory containing documents")
    parser.add_argument("--output", type=str, default="tests/evaluation/testset.csv", help="Output path for test set")
    parser.add_argument("--size", type=int, default=10, help="Number of questions to generate")
    
    args = parser.parse_args()
    
    # Create evaluation dir if not exists
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    
    generate_test_data(args.docs, args.output, args.size)
