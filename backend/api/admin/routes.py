"""
Admin API Routes
===============
Endpoints for admin operations (document upload, management).
"""

from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from pathlib import Path
import os
import tempfile
from backend.api.auth.routes import require_auth

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/upload', methods=['POST'])
@require_auth(admin_only=True)
def upload_document():
    """Upload and process a document."""
    if 'file' not in request.files:
        return jsonify({
            'success': False,
            'error': 'No file provided'
        }), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            'success': False,
            'error': 'No file selected'
        }), 400
    
    # Save uploaded file temporarily using system temp directory
    filename = secure_filename(file.filename)
    
    # Use Python's tempfile for better cross-platform temp handling
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as temp_file:
        temp_path = Path(temp_file.name)
        try:
            file.save(str(temp_path))
            
            # Get RAG service
            rag_service = current_app.config['RAG_SERVICE']
            
            # Process document
            result = rag_service.upload_document(temp_path, filename)
            
            if result['success']:
                return jsonify(result), 200
            else:
                return jsonify(result), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
        finally:
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()

@admin_bp.route('/documents', methods=['GET'])
@require_auth(admin_only=True)
def list_documents():
    """List uploaded documents. Excludes TestRail/Confluence sync files so admin only sees manually uploaded docs."""
    rag_service = current_app.config['RAG_SERVICE']
    all_docs = rag_service.list_documents()
    name = lambda d: (d.get('name') or '')
    manual_only = [
        d for d in all_docs
        if not name(d).startswith('testrail_') and not name(d).startswith('confluence_')
    ]
    return jsonify({
        'success': True,
        'documents': manual_only,
        'count': len(manual_only)
    }), 200

@admin_bp.route('/documents/<doc_id>', methods=['DELETE'])
@require_auth(admin_only=True)
def delete_document(doc_id):
    """Delete a document."""
    rag_service = current_app.config['RAG_SERVICE']
    result = rag_service.delete_document(doc_id)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 404

@admin_bp.route('/documents/<doc_id>/download', methods=['GET'])
@require_auth(admin_only=True)
def download_document(doc_id):
    """Download a document file."""
    from flask import send_file
    rag_service = current_app.config['RAG_SERVICE']
    
    # Find document by ID
    if doc_id not in rag_service.documents:
        return jsonify({
            'success': False,
            'error': 'Document not found'
        }), 404
    
    doc_info = rag_service.documents[doc_id]
    file_path = Path(doc_info['path']).resolve()

    # Path traversal protection: ensure file is under documents directory
    _docs_dir = Path(rag_service.documents_dir).resolve() if hasattr(rag_service, 'documents_dir') else None
    if _docs_dir and not str(file_path).startswith(str(_docs_dir)):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    if not file_path.exists():
        return jsonify({
            'success': False,
            'error': 'File not found on disk'
        }), 404
    
    try:
        return send_file(
            str(file_path.absolute()),
            as_attachment=True,
            download_name=doc_info['name']
        )
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@admin_bp.route('/stats', methods=['GET'])
@require_auth(admin_only=True)
def get_stats():
    """Get system statistics."""
    rag_service = current_app.config['RAG_SERVICE']
    stats = rag_service.get_stats()
    
    if stats['success']:
        return jsonify(stats), 200
    else:
        return jsonify(stats), 500

@admin_bp.route('/chromadb', methods=['GET'])
@require_auth(admin_only=True)
def get_chromadb_contents():
    """Get ChromaDB collection contents. Optional ?limit=N returns only first N chunks (faster)."""
    rag_service = current_app.config['RAG_SERVICE']
    limit = request.args.get('limit', type=int)
    result = rag_service.get_chromadb_contents(limit=limit)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 500

@admin_bp.route('/chromadb/reset', methods=['POST'])
@require_auth(admin_only=True)
def reset_chromadb():
    """Reset ChromaDB by deleting all collections, and clear all sync logs (TestRail + Confluence)."""
    rag_service = current_app.config['RAG_SERVICE']

    # Get delete_all parameter from request
    data = request.get_json() or {}
    delete_all = data.get('delete_all', True)

    result = rag_service.reset_chromadb(delete_all=delete_all)

    if result['success']:
        # Clear all sync logs so "Reset Knowledge Base" resets data and logs
        storage_dir = Path(os.getenv('STORAGE_DIR', 'storage'))
        sync_metadata_files = [
            storage_dir / 'testrail_sync_metadata.json',
            storage_dir / 'confluence_sync_metadata.json',
        ]
        cleared = []
        for f in sync_metadata_files:
            if f.exists():
                try:
                    f.unlink()
                    cleared.append(f.name)
                except Exception as e:
                    if 'details' not in result:
                        result['details'] = result.get('details') or {}
                    result.setdefault('sync_log_errors', []).append(f"{f.name}: {e}")
        if cleared:
            result['details'] = result.get('details') or {}
            result['details']['sync_logs_cleared'] = cleared
            result['message'] = (result.get('message', '') or '').rstrip('.') + f". Cleared sync logs: {', '.join(cleared)}."
        return jsonify(result), 200
    else:
        return jsonify(result), 500


@admin_bp.route('/sync/testrail', methods=['POST'])
@require_auth(admin_only=True)
def sync_testrail():
    """Trigger TestRail sync in background."""
    try:
        from backend.services.sync_service import TestRailSyncService
        import threading
        
        sync_service = TestRailSyncService()
        
        # Check if sync is already running
        status = sync_service.get_sync_status()
        if status.get('is_syncing', False):
             return jsonify({
                'success': False, 
                'message': 'Sync execution is already in progress'
            }), 409

        # Use the app's shared RAG service so customer portal sees synced documents
        app = current_app._get_current_object()
        rag_service = app.config.get('RAG_SERVICE')

        # Start sync in background thread (pass rag_service so same instance is updated)
        def run_sync():
            try:
                with app.app_context():
                    sync_svc = TestRailSyncService()
                    sync_svc.sync_from_testrail(rag_service=rag_service)
            except Exception as e:
                print(f"Background sync failed: {e}")

        thread = threading.Thread(target=run_sync)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            'success': True,
            'message': 'TestRail sync started in background',
            'status': 'started'
        }), 202
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': f'Failed to start sync: {str(e)}'
        }), 500


@admin_bp.route('/sync/confluence', methods=['POST'])
@require_auth(admin_only=True)
def sync_confluence():
    """Trigger Confluence sync (CQL) in background."""
    try:
        from backend.services.confluence_sync_service import ConfluenceSyncService
        import threading

        sync_service = ConfluenceSyncService()

        status = sync_service.get_sync_status()
        if status.get('is_syncing', False):
            return jsonify({
                'success': False,
                'message': 'Confluence sync is already in progress'
            }), 409

        app = current_app._get_current_object()
        rag_service = app.config.get('RAG_SERVICE')

        def run_sync():
            try:
                with app.app_context():
                    sync_svc = ConfluenceSyncService()
                    sync_svc.sync_from_confluence(rag_service=rag_service)
            except Exception as e:
                print(f"Confluence sync failed: {e}")

        thread = threading.Thread(target=run_sync)
        thread.daemon = True
        thread.start()

        return jsonify({
            'success': True,
            'message': 'Confluence sync started in background (CQL)',
            'status': 'started'
        }), 202

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': f'Failed to start Confluence sync: {str(e)}'
        }), 500


@admin_bp.route('/confluence-diagnose', methods=['GET'])
@require_auth(admin_only=True)
def confluence_diagnose():
    """
    Run Confluence connectivity diagnostic to determine 404 cause:
    credentials (401/403), API path (404), or CQL/query (400 or 0 results).
    """
    try:
        from backend.connectors.confluence_connector import ConfluenceConnector
        from backend.rag.settings import get_config

        config = get_config()
        url = getattr(config, "confluence_url", None) or os.getenv("CONFLUENCE_URL", "")
        email = getattr(config, "confluence_email", None) or os.getenv("CONFLUENCE_EMAIL", "")
        api_token = getattr(config, "confluence_api_token", None) or os.getenv("CONFLUENCE_API_TOKEN", "")
        cql = getattr(config, "confluence_cql", None) or os.getenv("CONFLUENCE_CQL", "type=page")

        if not url or not email or not api_token:
            return jsonify({
                "success": False,
                "error": "Missing CONFLUENCE_URL, CONFLUENCE_EMAIL, or CONFLUENCE_API_TOKEN",
                "diagnose": None,
            }), 400

        conn = ConfluenceConnector(url=url, email=email, api_token=api_token)
        result = conn.diagnose(cql=cql or None)
        return jsonify({"success": True, "diagnose": result}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "diagnose": None}), 500


@admin_bp.route('/sync/status', methods=['GET'])
@require_auth(admin_only=True)
def get_sync_status():
    """Get sync status. Returns TestRail status (backward compat); Confluence status nested."""
    try:
        from backend.services.sync_service import TestRailSyncService
        from backend.services.confluence_sync_service import ConfluenceSyncService

        testrail_svc = TestRailSyncService()
        confluence_svc = ConfluenceSyncService()

        testrail_status = testrail_svc.get_sync_status()
        confluence_status = confluence_svc.get_sync_status()

        # Merge TestRail at top level for backward compat with existing UI
        status = dict(testrail_status)
        status['confluence'] = confluence_status

        return jsonify({
            'success': True,
            'status': status
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@admin_bp.route('/settings/public', methods=['GET'])
def get_public_settings():
    """Return non-sensitive public settings (e.g. default theme). No auth required."""
    try:
        svc = current_app.config.get('SETTINGS_SERVICE')
        theme = svc.get('default_theme', 'dark') if svc else 'dark'
        return jsonify({'success': True, 'default_theme': theme}), 200
    except Exception as e:
        return jsonify({'success': True, 'default_theme': 'dark'}), 200


@admin_bp.route('/settings', methods=['GET'])
@require_auth(admin_only=True)
def get_settings():
    """Return settings schema and current values. Sensitive values are masked as ****."""
    try:
        svc = current_app.config.get('SETTINGS_SERVICE')
        if not svc:
            return jsonify({'success': False, 'error': 'Settings service not available'}), 500
        return jsonify({'success': True, **svc.get_all_for_api()}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/settings', methods=['PUT'])
@require_auth(admin_only=True)
def update_settings():
    """Save settings. Sensitive fields submitted as **** are not overwritten."""
    try:
        svc = current_app.config.get('SETTINGS_SERVICE')
        if not svc:
            return jsonify({'success': False, 'error': 'Settings service not available'}), 500
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Invalid request body'}), 400
        svc.set_many(data)
        # Reconfigure scheduler in case schedule settings changed
        scheduler = current_app.config.get('SCHEDULER_SERVICE')
        if scheduler:
            scheduler.reconfigure()
        return jsonify({
            'success': True,
            'message': 'Settings saved and applied successfully',
            **svc.get_all_for_api()
        }), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/sync/schedule', methods=['GET'])
@require_auth(admin_only=True)
def get_sync_schedule():
    """Return next scheduled run times for TestRail and Confluence syncs."""
    try:
        scheduler = current_app.config.get('SCHEDULER_SERVICE')
        if not scheduler:
            return jsonify({'success': True, 'testrail': {'scheduled': False}, 'confluence': {'scheduled': False}}), 200
        return jsonify({'success': True, **scheduler.get_schedule_info()}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
