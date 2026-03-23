"""
RAG System Backend Application
==============================
Main Flask application for RAG system deployment.
"""

import os
import sys
import warnings
from pathlib import Path
from flask import Flask, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# Suppress urllib3 OpenSSL warning on macOS (LibreSSL compatibility)
warnings.filterwarnings('ignore', category=UserWarning, module='urllib3')

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables
env_path = Path(__file__).parent.parent / 'config' / '.env'
if env_path.exists():
    load_dotenv(env_path)
else:
    # Try example file
    env_example = Path(__file__).parent.parent / 'config' / 'env.example'
    if env_example.exists():
        load_dotenv(env_example)

# Import API routes
from backend.api.admin.routes import admin_bp
from backend.api.customer.routes import customer_bp
from backend.api.auth.routes import auth_bp
from backend.services.rag_service import RAGService
from backend.services.auth_service import AuthService
from backend.services.settings_service import SettingsService
from backend.services.scheduler_service import SchedulerService

def create_app():
    """Create and configure Flask application."""
    app = Flask(__name__,
                static_folder='../frontend',
                template_folder='../frontend')

    # Configuration
    _secret = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    if _secret == 'dev-secret-key-change-in-production' and os.getenv('FLASK_DEBUG', 'False').lower() != 'true':
        print("⚠️  WARNING: Using default SECRET_KEY — set SECRET_KEY env var for production!")
    app.config['SECRET_KEY'] = _secret
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('ADMIN_UPLOAD_MAX_SIZE_MB', 50)) * 1024 * 1024
    app.config['PERMANENT_SESSION_LIFETIME'] = 7200  # 2 hours

    # CORS: restrict origins in production (set CORS_ALLOWED_ORIGINS env var)
    _cors_origins = os.getenv('CORS_ALLOWED_ORIGINS', '*').split(',')
    _cors_origins = [o.strip() for o in _cors_origins if o.strip()]
    CORS(app, resources={
        r"/api/*": {"origins": _cors_origins, "supports_credentials": True},
        r"/admin/*": {"origins": _cors_origins, "supports_credentials": True},
        r"/customer/*": {"origins": _cors_origins, "supports_credentials": True}
    })

    # Initialize SettingsService FIRST — applies stored settings to os.environ
    # before RAGService / RAGConfig is instantiated so stored settings take effect.
    settings_service = SettingsService()
    app.config['SETTINGS_SERVICE'] = settings_service

    # Initialize services (RAGService reads RAGConfig which reads os.environ)
    rag_service = RAGService()
    auth_service = AuthService()

    app.config['RAG_SERVICE'] = rag_service
    app.config['AUTH_SERVICE'] = auth_service

    # Start background scheduler for daily syncs (needs app ref for app_context)
    scheduler_service = SchedulerService(settings_service, app)
    app.config['SCHEDULER_SERVICE'] = scheduler_service

    # Pre-warm the exact-scan embedding caches for specs and testcases in the background
    # so the first requirement analysis request never pays the cold-start penalty
    # (loading ~14 000 embeddings × 6 KB from SQLite on first use).
    import threading
    def _prewarm_exact_scan_cache():
        try:
            import numpy as _np
            vs = rag_service.load_fresh_vectorstore_once()
            if vs is None:
                return
            rag_obj = rag_service.rag
            old_vs = rag_obj.vectorstore
            rag_obj.vectorstore = vs
            dummy_q = "account payment transfer"
            dummy_emb = _np.array(rag_obj.embeddings.embed_query(dummy_q), dtype=float)
            dummy_norm = dummy_emb / (_np.linalg.norm(dummy_emb) + 1e-10)
            for filt in [{"source_type": "specs"}, {"source_type": "testcase"}]:
                cache_key = repr(sorted(filt.items()))
                if cache_key in rag_obj._exact_emb_cache:
                    continue
                try:
                    col = vs._collection
                    res = col.get(where=filt, include=["documents", "metadatas", "embeddings"])
                    embs = res.get("embeddings")
                    if embs is not None and len(embs) > 0:
                        E = _np.array(embs, dtype=float)
                        norms = _np.linalg.norm(E, axis=1, keepdims=True)
                        E_norm = E / (norms + 1e-10)
                        rag_obj._exact_emb_cache[cache_key] = (
                            E_norm,
                            res.get("documents") or [],
                            res.get("metadatas") or [],
                        )
                        print(f"[startup] Pre-warmed exact-scan cache: {len(embs)} docs for {cache_key}")
                except Exception as e:
                    print(f"[startup] Pre-warm failed for {filt}: {e}")
            rag_obj.vectorstore = old_vs
        except Exception as e:
            print(f"[startup] Pre-warm thread error: {e}")
    threading.Thread(target=_prewarm_exact_scan_cache, daemon=True, name="exact-scan-prewarm").start()
    
    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(customer_bp, url_prefix='/api/customer')
    
    # Serve frontend files
    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'customer/index.html')
    
    @app.route('/admin')
    def admin():
        return send_from_directory(app.static_folder, 'admin/index.html')
    
    @app.route('/admin/login')
    def admin_login():
        return send_from_directory(app.static_folder, 'admin/login.html')
    
    @app.route('/customer')
    def customer():
        return send_from_directory(app.static_folder, 'customer/index.html')
    
    @app.route('/<path:path>')
    def serve_static(path):
        """Serve static files."""
        return send_from_directory(app.static_folder, path)
    
    @app.route('/health')
    def health():
        """Health check endpoint."""
        return {'status': 'healthy', 'service': 'rag-system'}, 200

    # Error handlers — prevent stack trace leaks
    @app.errorhandler(404)
    def not_found(e):
        return {'success': False, 'error': 'Not found'}, 404

    @app.errorhandler(500)
    def server_error(e):
        return {'success': False, 'error': 'Internal server error'}, 500

    # Security headers
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response

    return app

if __name__ == '__main__':
    app = create_app()
    port = int(os.getenv('PORT', 5001))
    host = os.getenv('HOST', '0.0.0.0')
    debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    print(f"🚀 Starting RAG System Server...")
    print(f"   Admin Interface: http://{host}:{port}/admin")
    print(f"   Customer Interface: http://{host}:{port}/customer")
    print(f"   API Base: http://{host}:{port}/api")
    
    app.run(host=host, port=port, debug=debug, threaded=True)

