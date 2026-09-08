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
from backend.api.agents.proxy import _forward_json
from backend.services import analytics_service

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
        from backend.services.testrail_sync_service import TestRailSyncService
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
        from backend.rag.rag_settings import get_config

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
        from backend.services.testrail_sync_service import TestRailSyncService
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


@admin_bp.route('/agent-settings', methods=['GET'])
@require_auth(admin_only=True)
def get_agent_settings():
    """Proxy the QA-Agent-Network settings schema + values for the Agent Settings page.

    Deliberately served from the admin blueprint rather than /api/agents/*: the
    agents proxy enforces no auth at all (see the comment at the top of
    proxy.py), and this endpoint's sibling PUT writes GITHUB_TOKEN.
    """
    return _forward_json('GET', '/settings')


@admin_bp.route('/agent-settings', methods=['PUT'])
@require_auth(admin_only=True)
def update_agent_settings():
    """Save agent settings to QA-Agent-Network's config/.env. Admin only."""
    return _forward_json('PUT', '/settings')


@admin_bp.route('/analytics', methods=['DELETE'])
@require_auth(admin_only=True)
def clear_analytics():
    """Clear analytics and history for a specific user or all users over a specific window."""
    user_id_param = (request.args.get('user_id') or '').strip() or None
    window_param = (request.args.get('window') or '7d').strip()
    
    # 1. Clear AI-Test-Studio analytics (operations/requirements)
    analytics_service.clear_analytics(user_id=user_id_param, window=window_param)
    
    # 2. Proxy request to QA-Agent-Network to clear agent runs & audit history
    try:
        url = f'/analytics/clear?window={window_param}'
        if user_id_param:
            url += f'&user_id={user_id_param}'
        _forward_json('DELETE', url)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    
    return jsonify({"success": True})


@admin_bp.route('/analytics', methods=['GET'])
@require_auth(admin_only=True)
def get_analytics():
    """Combined time/cost analytics across every AI flow in the Studio.

    Admin-only for the same documented reason as agent-settings: /api/agents/*
    enforces no auth at all, and spend is not customer-facing data.

    The two halves are returned SEPARATELY and never summed into one figure:
    agent cost is reported by the Claude CLI (exact), Studio cost is estimated
    from a token rate card. Time saved is applied here, since this is where the
    human-minutes baselines live.
    """
    # No explicit window means "use the configured default", so the dashboard
    # can open on it without having to know it in advance.
    svc = current_app.config.get('SETTINGS_SERVICE')
    default_window = '7d'
    try:
        if svc:
            default_window = (svc.get('analytics_default_window', '7d') or '7d').strip()
    except Exception:
        default_window = '7d'
    if default_window not in analytics_service.WINDOWS:
        default_window = '7d'

    window = (request.args.get('window') or default_window).strip()
    if window not in analytics_service.WINDOWS:
        return jsonify({'success': False,
                        'error': "window must be one of "
                                 + ', '.join(analytics_service.WINDOWS)}), 400

    user_id_param = (request.args.get('user_id') or '').strip()
    studio = analytics_service.query(window, user_id=user_id_param or None)

    # The agent half comes from QA-Agent-Network; a dashboard must still render
    # if that server is down, so a failure degrades to an empty half plus a note.
    agents, agents_error = {}, None
    try:
        url = f'/analytics/summary?window={window}'
        if user_id_param:
            url += f'&user_id={user_id_param}'
        response = _forward_json('GET', url)
        # _forward_json returns a Response, or (Response, status) on failure —
        # and its failure bodies are dicts too, so "is a dict" is not enough to
        # call it a success. Without checking the status and the payload shape,
        # a stopped agent server renders as a silently empty section.
        status = response[1] if isinstance(response, tuple) else 200
        body = response[0] if isinstance(response, tuple) else response
        payload = body.get_json(silent=True) if hasattr(body, 'get_json') else None

        if status >= 400 or not isinstance(payload, dict) or 'overall' not in payload:
            detail = ''
            if isinstance(payload, dict):
                detail = payload.get('detail') or payload.get('error') or ''
            if status == 404:
                # Almost always a running-but-stale agent server: a bare
                # "not found" gives no clue which of the two servers is missing
                # the route, and the answer is nearly always that one of them
                # is still on pre-update code.
                agents_error = ('Analytics endpoint not found on the QA Agent '
                                'Network server (404). Restart it '
                                '(`bash scripts/run-server.sh`) so it picks up '
                                'the /analytics/summary route.')
            else:
                agents_error = detail or f'agent server returned HTTP {status}'
        else:
            agents = payload
    except Exception as exc:
        agents_error = str(exc)

    baselines = _analytics_baselines()
    return jsonify({
        'success': True,
        'window': window,
        'default_window': default_window,
        'baselines': baselines,
        'agents': agents,
        'agents_error': agents_error,
        'studio': studio,
        'time_saved': _time_saved(agents, studio, baselines),
    })


def _analytics_baselines() -> dict:
    """Human-minutes-per-outcome, from settings. One home for all four flows."""
    svc = current_app.config.get('SETTINGS_SERVICE')

    def _get(key, default):
        try:
            return float(svc.get(key, default) if svc else default)
        except (TypeError, ValueError, AttributeError):
            return float(default)
    return {
        'min_per_test_authored': _get('analytics_min_per_test_authored', 120),
        'min_per_test_fixed': _get('analytics_min_per_test_fixed', 45),
        'min_per_test_adapted': _get('analytics_min_per_test_adapted', 30),
        'min_per_test_case_written': _get('analytics_min_per_test_case_written', 15),
    }


def _time_saved(agents: dict, studio: dict, baselines: dict) -> dict:
    """Estimated human minutes saved, minus the wall time the machine spent."""
    overall = (agents or {}).get('overall') or {}
    outcomes = (studio or {}).get('outcomes') or {}

    agent_gross = (
        int(overall.get('tests_created') or 0) * baselines['min_per_test_authored']
        + int(overall.get('tests_fixed') or 0) * baselines['min_per_test_fixed']
        + int(overall.get('items_adapted') or 0) * baselines['min_per_test_adapted']
    )
    studio_gross = (
        (int(outcomes.get('test_cases_generated') or 0)
         + int(outcomes.get('e2e_tests_generated') or 0))
        * baselines['min_per_test_case_written']
    )
    agent_spent = float(overall.get('duration_s') or 0.0) / 60.0
    studio_spent = float((studio or {}).get('run_duration_s') or 0.0) / 60.0
    return {
        'agents_min': max(0.0, round(agent_gross - agent_spent, 1)),
        'studio_min': max(0.0, round(studio_gross - studio_spent, 1)),
        'total_min': max(0.0, round(agent_gross + studio_gross - agent_spent - studio_spent, 1)),
        'basis': 'estimate',
    }


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
