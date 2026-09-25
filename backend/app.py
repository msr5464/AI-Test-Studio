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
from backend.api.agents.proxy import agents_bp
from backend.services.rag_service import RAGService
from backend.services.auth_service import AuthService
from backend.services.settings_service import SettingsService
from backend.services.scheduler_service import SchedulerService

def _clear_interrupted_syncs():
    """Clear a sync left "in progress" by the previous process.

    Syncs run in threads of this process, so any is_syncing flag found at startup
    belongs to a sync that died with the last one. Left alone, Connectors showed
    "Syncing…" and refused new syncs (409) until the 30-minute stale guard fired.
    """
    from backend.services.testrail_sync_service import TestRailSyncService
    from backend.services.confluence_sync_service import ConfluenceSyncService
    for service_cls in (TestRailSyncService, ConfluenceSyncService):
        try:
            svc = service_cls()
            metadata = svc._load_sync_metadata()
            if metadata.get('is_syncing'):
                metadata['is_syncing'] = False
                svc._save_sync_metadata(metadata)
                svc._append_sync_log("Interrupted: the app restarted before this sync finished.")
                print(f"⚠️  {service_cls.__name__}: cleared a sync interrupted by the last restart")
        except Exception as e:
            print(f"⚠️  Could not check {service_cls.__name__} for an interrupted sync: {e}")


def create_app():
    """Create and configure Flask application."""
    app = Flask(__name__,
                static_folder='../frontend',
                template_folder='../frontend')

    # Configuration
    #
    # SECRET_KEY signs the session cookie, and the session cookie is the ONLY
    # thing separating a visitor from an admin. Every value below is published
    # in this repository — the code fallback, and the placeholder shipped in
    # config/env.example — so knowing one is enough to mint a valid admin
    # cookie: user ids are md5(username)[:12], making the admin's id derivable
    # too (and QA-Agent-Network hardcodes it as 21232f297a57).
    #
    # The previous guard compared only against the CODE fallback while the live
    # value came from config/.env as the env.example placeholder. The two
    # differ, so the warning never printed once, and a warning would have been
    # too weak regardless.
    _INSECURE_SECRETS = {
        'dev-secret-key-change-in-production',
        'your-secret-key-here-change-in-production',
        'change-me', 'secret', '',
    }
    _secret = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    _debug = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    if _secret.strip() in _INSECURE_SECRETS:
        if not _debug:
            raise RuntimeError(
                "SECRET_KEY is unset or set to a publicly known placeholder. "
                "Session cookies signed with it can be forged by anyone who has "
                "read this repository, including as an admin. Generate one with "
                "`python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"` "
                "and set SECRET_KEY, or set FLASK_DEBUG=true for local development."
            )
        print("⚠️  WARNING: placeholder SECRET_KEY — development only, sessions are forgeable")
    app.config['SECRET_KEY'] = _secret

    # Cookie hardening. None of these were set: Secure defaults to False, so the
    # session rode plaintext HTTP, and SameSite was unset entirely.
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
    app.config['SESSION_COOKIE_SECURE'] = (
        os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('ADMIN_UPLOAD_MAX_SIZE_MB', 50)) * 1024 * 1024
    app.config['PERMANENT_SESSION_LIFETIME'] = 7200  # 2 hours

    # CORS. The default was '*' WITH supports_credentials=True below, which
    # Flask-CORS resolves by reflecting the caller's Origin and setting
    # Access-Control-Allow-Credentials: true — so any site on the internet could
    # make credentialed, readable requests against a live admin session, and
    # there is no CSRF token anywhere in this codebase. The key was also absent
    # from config/env.example, so nobody was ever prompted to set it.
    _cors_origins = os.getenv('CORS_ALLOWED_ORIGINS', '').split(',')
    _cors_origins = [o.strip() for o in _cors_origins if o.strip()]
    if not _cors_origins:
        _port = os.getenv('PORT', '5001')
        _cors_origins = [f"http://localhost:{_port}", f"http://127.0.0.1:{_port}"]
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

    _clear_interrupted_syncs()

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
        except Exception as e:
            print(f"[startup] Pre-warm thread error: {e}")
    threading.Thread(target=_prewarm_exact_scan_cache, daemon=True, name="exact-scan-prewarm").start()
    
    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(customer_bp, url_prefix='/api/customer')
    app.register_blueprint(agents_bp, url_prefix='/api/agents')
    
    # Serve frontend files. Each customer page has its own URL so a reload keeps
    # the page and it can be bookmarked; keep in sync with TAB_PATHS in customer/index.html.
    @app.route('/')
    @app.route('/customer')
    @app.route('/test-generator')
    @app.route('/authoring-agent')
    @app.route('/healing-agent')
    @app.route('/adaptation-agent')
    @app.route('/talk-to-tests')
    def index():
        return send_from_directory(app.static_folder, 'customer/index.html')
    
    @app.route('/admin')
    def admin():
        return send_from_directory(app.static_folder, 'admin/index.html')
    
    @app.route('/admin/login')
    def admin_login():
        return send_from_directory(app.static_folder, 'admin/login.html')
    
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

