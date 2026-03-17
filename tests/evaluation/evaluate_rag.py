import os
import sys
import argparse
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from dotenv import load_dotenv
from tqdm import tqdm

# Project root (tests/evaluation/evaluate_rag.py -> parent.parent.parent)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.rag.multi_format_rag import MultiFormatRAG
from backend.rag.settings import get_config

# Import evaluation specific dependencies
from datasets import Dataset
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    AnswerCorrectness
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.chat_models import ChatOllama
from langchain_huggingface import HuggingFaceEmbeddings
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    GEMINI_AVAILABLE = True
except ImportError:
    ChatGoogleGenerativeAI = None
    GEMINI_AVAILABLE = False

def run_evaluation(testset_path: str, output_dir: str):
    load_dotenv(_PROJECT_ROOT / 'config' / '.env')
    
    # 1. Load Testset
    print(f"📋 Loading test set from {testset_path}...")
    test_df = pd.read_csv(testset_path)
    
    # Required columns for Ragas: question, contexts, answer, ground_truth
    if not all(col in test_df.columns for col in ['question', 'ground_truth']):
        print(f"❌ Testset must contain 'question' and 'ground_truth' columns.")
        return

    # 2. Initialize RAG System
    print("🤖 Initializing RAG system...")
    from backend.services.rag_service import RAGService
    service = RAGService()
    rag = service.rag
    # Ensure all matching sources are included for Ragas evaluation
    rag.show_matching_sources = True

    # 3. Collect RAG responses
    print("🚀 Running RAG pipeline on test set...")
    questions = test_df['question'].tolist()
    ground_truths = test_df['ground_truth'].tolist()
    
    answers = []
    contexts = []
    
    for question in tqdm(questions):
        try:
            result = rag.query(question, bypass_cache=True)
            answers.append(result.get('answer', ""))
            
            # Extract context strings from source documents
            source_docs = result.get('source_documents', [])
            # Ragas expects context as a list of strings
            context_list = []
            for doc in source_docs:
                if isinstance(doc, dict):
                    context_list.append(doc.get('content', ""))
                elif hasattr(doc, 'page_content'):
                    context_list.append(doc.page_content)
                else:
                    context_list.append(str(doc))
            contexts.append(context_list)
        except Exception as e:
            print(f"⚠️ Error querying for: {question[:50]}... -> {e}")
            answers.append("ERROR")
            contexts.append([])

    # 4. Prepare Dataset for Ragas
    data = {
        "question": questions,
        "answer": answers,
        "contexts": contexts,
        "ground_truth": ground_truths
    }
    dataset = Dataset.from_dict(data)

    # 5. Setup LLMs for Evaluation (Judges)
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    gemini_key = (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

    if llm_provider == "openai":
        eval_llm = ChatOpenAI(model="gpt-4o")
        eval_embeddings = OpenAIEmbeddings()
    elif llm_provider == "gemini" and gemini_key and GEMINI_AVAILABLE:
        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        if gemini_model in ("gemini-1.5-flash", "gemini-1.5-pro"):
            gemini_model = "gemini-2.5-flash"
        eval_llm = ChatGoogleGenerativeAI(model=gemini_model, temperature=0, google_api_key=gemini_key)
        eval_embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    else:
        # Using Ollama as judge
        eval_llm = ChatOllama(model=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
        eval_embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    # Wrap for Ragas
    llm_judge = LangchainLLMWrapper(eval_llm)
    emb_judge = LangchainEmbeddingsWrapper(eval_embeddings)

    # 6. Run Ragas Evaluation
    print("📊 Computing RAGAS metrics...")
    metrics = [
        Faithfulness(),
        AnswerRelevancy(),
        ContextPrecision(),
        ContextRecall(),
        AnswerCorrectness()
    ]
    
    result = evaluate(
        dataset,
        metrics=metrics,
        llm=llm_judge,
        embeddings=emb_judge
    )

    # 7. Save and Report
    print("\n📝 Evaluation Results:")
    print(result)
    
    report_df = result.to_pandas()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(output_dir) / f"evaluation_report_{timestamp}.csv"
    report_df.to_csv(output_file, index=False)
    
    # Save summary
    summary_file = Path(output_dir) / f"evaluation_summary_{timestamp}.txt"
    with open(summary_file, 'w') as f:
        f.write(f"RAGAS Evaluation Summary - {timestamp}\n")
        f.write("="*40 + "\n")
        # In Ragas 0.4.x, result is an EvaluationResult object, use .scores
        scores = getattr(result, 'scores', result)
        if isinstance(scores, dict):
            for metric, score in scores.items():
                f.write(f"{metric}: {score}\n")
        else:
            f.write(str(result))
    
    print(f"✅ Full report saved to {output_file}")
    print(f"✅ Summary saved to {summary_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Knowledge-AI RAG performance")
    parser.add_argument("--testset", type=str, default="tests/evaluation/testset.csv", help="Path to input test set CSV")
    parser.add_argument("--output", type=str, default="tests/evaluation/reports", help="Directory for reports")
    
    args = parser.parse_args()
    
    Path(args.output).mkdir(parents=True, exist_ok=True)
    
    run_evaluation(args.testset, args.output)
