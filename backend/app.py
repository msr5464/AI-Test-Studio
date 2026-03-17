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

def create_app():
    """Create and configure Flask application."""
    app = Flask(__name__, 
                static_folder='../frontend',
                template_folder='../frontend')
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('ADMIN_UPLOAD_MAX_SIZE_MB', 50)) * 1024 * 1024
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours
    
    # Enable CORS with credentials support for session cookies
    CORS(app, resources={
        r"/api/*": {"origins": "*", "supports_credentials": True},
        r"/admin/*": {"origins": "*", "supports_credentials": True},
        r"/customer/*": {"origins": "*", "supports_credentials": True}
    })
    
    # Initialize services
    rag_service = RAGService()
    auth_service = AuthService()
    
    app.config['RAG_SERVICE'] = rag_service
    app.config['AUTH_SERVICE'] = auth_service
    
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
    
    app.run(host=host, port=port, debug=debug)

