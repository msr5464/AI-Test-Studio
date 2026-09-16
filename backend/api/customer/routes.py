"""
Customer API Routes
==================
Endpoints for customer operations (querying RAG system).
"""

import os
import time
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename
import tempfile

from flask import Blueprint, request, jsonify, current_app, Response, session

from backend.services import requirement_runs
from backend.services.requirement_analysis_service import RequirementAnalysisService
from backend.api.auth.routes import require_auth

customer_bp = Blueprint('customer', __name__)


@customer_bp.before_request
@require_auth(admin_only=False)
def check_auth():
    """Enforce authentication on every customer route.

    These were entirely unauthenticated. The UI hides the app shell behind an
    overlay until login, but that is client-side decoration — curl ignores it —
    so anyone who could reach the port could run requirement analysis (LLM spend
    on the server's keys), query the RAG corpus, and create or update TestRail
    cases using the server's stored TestRail credentials, without an account at
    all. That also defeated the approval gate: a user sitting in
    pending_approval had the same access as an approved one.

    Mirrors the pattern already used by the agent proxy blueprint. Decorator
    order matters: require_auth wraps first so its error response short-circuits
    the request.
    """
    pass


def _get_testrail_connector():
    """Build TestRail connector from config/env. Returns None if not configured."""
    from backend.rag.rag_settings import get_config
    config = get_config()
    url = getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
    email = getattr(config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
    api_key = getattr(config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
    if not url or not email or not api_key:
        return None
    from backend.connectors.testrail_connector import TestRailConnector
    return TestRailConnector(url=url, email=email, api_key=api_key)


@customer_bp.route('/config', methods=['GET'])
def customer_config():
    """Return public config for the frontend (e.g. TestRail base URL for case links)."""
    from backend.rag.rag_settings import get_config
    config = get_config()
    testrail_url = (getattr(config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "") or "").rstrip("/")
    return jsonify({"testrail_url": testrail_url}), 200


@customer_bp.route('/requirement-analysis', methods=['POST'])
def requirement_analysis():
    """
    Analyze requirement spec: find related tests, identify uncovered requirements, generate new tests.

    Input (provide exactly one):
    - requirement_spec: Pasted text (JSON body)
    - confluence_url: Confluence page URL (JSON body)
    - file: Uploaded file (PDF, DOCX, TXT) (multipart/form-data)

    Options (JSON body or form):
    - generate_new_tests: bool (default: true)
    """
    file_paths = []
    try:
        text = None
        confluence_urls = []

        if request.is_json:
            data = request.get_json() or {}
            text = data.get("requirement_spec", "").strip() or None
            _raw_urls = data.get("confluence_urls") or data.get("confluence_url") or ""
            if isinstance(_raw_urls, list):
                confluence_urls = [u.strip() for u in _raw_urls if u.strip()]
            else:
                confluence_urls = [u.strip() for u in str(_raw_urls).splitlines() if u.strip()]
            generate_new = data.get("generate_new_tests", True)
            generate_p2_p3 = data.get("generate_p2_p3_tests", False)
            push_to_testrail = data.get("push_to_testrail", False)
            target_section_id = data.get("target_section_id")
            use_section_of_related = data.get("use_section_of_related", False)
        else:
            data = request.form
            text = (data.get("requirement_spec") or "").strip() or None
            _raw_urls = data.get("confluence_urls") or data.get("confluence_url") or ""
            if isinstance(_raw_urls, list):
                confluence_urls = [u.strip() for u in _raw_urls if u.strip()]
            else:
                confluence_urls = [u.strip() for u in str(_raw_urls).splitlines() if u.strip()]
            generate_new = data.get("generate_new_tests", "true").lower() in ("true", "1", "yes")
            generate_p2_p3 = (data.get("generate_p2_p3_tests") or "false").lower() in ("true", "1", "yes")
            push_to_testrail = (data.get("push_to_testrail") or "false").lower() in ("true", "1", "yes")
            target_section_id = data.get("target_section_id")
            use_section_of_related = (data.get("use_section_of_related") or "false").lower() in ("true", "1", "yes")
        if target_section_id is not None:
            try:
                target_section_id = int(target_section_id)
            except (TypeError, ValueError):
                target_section_id = None

        for f in request.files.getlist("file"):
            if not f.filename:
                continue
            ext = Path(secure_filename(f.filename)).suffix.lower()
            if ext not in (".txt", ".pdf", ".docx", ".doc"):
                return jsonify({"success": False, "error": f"Unsupported file type: {f.filename}. Use .txt, .pdf, or .docx"}), 400
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                f.save(tmp.name)
                file_paths.append(Path(tmp.name))

        _has_text = bool(text)
        _has_files = len(file_paths) > 0
        _has_urls = len(confluence_urls) > 0
        _input_types = sum([_has_text, _has_files, _has_urls])
        if _input_types == 0:
            return jsonify({"success": False, "error": "Provide requirement_spec (text), file(s), or confluence URL(s)"}), 400
        if _input_types > 1:
            return jsonify({"success": False, "error": "Provide only one type of input: text, file(s), or URL(s) — not a mix"}), 400

        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)

        result = svc.analyze(
            text=text,
            file_path=file_paths[0] if len(file_paths) == 1 else None,
            file_paths=file_paths if len(file_paths) > 1 else None,
            confluence_url=confluence_urls[0] if len(confluence_urls) == 1 else None,
            confluence_urls=confluence_urls if len(confluence_urls) > 1 else None,
            generate_new_tests=generate_new,
            generate_p2_p3_tests=generate_p2_p3,
            push_to_testrail=push_to_testrail,
            target_section_id=target_section_id,
            use_section_of_related=use_section_of_related,
        )

        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        for fp in file_paths:
            if fp and fp.exists():
                try: fp.unlink()
                except Exception: pass


def _requirement_analysis_params():
    """Parse requirement-analysis input and options from request. Returns (text, file_paths, confluence_urls, opts) or raises."""
    text = None
    file_paths = []
    confluence_urls = []
    if request.is_json:
        data = request.get_json() or {}
        text = data.get("requirement_spec", "").strip() or None
        _raw_urls = data.get("confluence_urls") or data.get("confluence_url") or ""
        if isinstance(_raw_urls, list):
            confluence_urls = [u.strip() for u in _raw_urls if u.strip()]
        else:
            confluence_urls = [u.strip() for u in str(_raw_urls).splitlines() if u.strip()]
        generate_new = data.get("generate_new_tests", True)
        generate_p2_p3 = data.get("generate_p2_p3_tests", False)
        push_to_testrail = data.get("push_to_testrail", False)
        target_section_id = data.get("target_section_id")
        use_section_of_related = data.get("use_section_of_related", False)
    else:
        data = request.form
        text = (data.get("requirement_spec") or "").strip() or None
        _raw_urls = data.get("confluence_urls") or data.get("confluence_url") or ""
        if isinstance(_raw_urls, list):
            confluence_urls = [u.strip() for u in _raw_urls if u.strip()]
        else:
            confluence_urls = [u.strip() for u in str(_raw_urls).splitlines() if u.strip()]
        generate_new = (data.get("generate_new_tests") or "true").lower() in ("true", "1", "yes")
        generate_p2_p3 = (data.get("generate_p2_p3_tests") or "false").lower() in ("true", "1", "yes")
        push_to_testrail = (data.get("push_to_testrail") or "false").lower() in ("true", "1", "yes")
        target_section_id = data.get("target_section_id")
        use_section_of_related = (data.get("use_section_of_related") or "false").lower() in ("true", "1", "yes")
    if target_section_id is not None:
        try:
            target_section_id = int(target_section_id)
        except (TypeError, ValueError):
            target_section_id = None
    for f in request.files.getlist("file"):
        if not f.filename:
            continue
        ext = Path(secure_filename(f.filename)).suffix.lower()
        if ext not in (".txt", ".pdf", ".docx", ".doc"):
            raise ValueError(f"Unsupported file type: {f.filename}. Use .txt, .pdf, or .docx")
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            f.save(tmp.name)
            file_paths.append(Path(tmp.name))
    _has_text = bool(text)
    _has_files = len(file_paths) > 0
    _has_urls = len(confluence_urls) > 0
    _input_types = sum([_has_text, _has_files, _has_urls])
    if _input_types == 0:
        raise ValueError("Provide requirement_spec (text), file(s), or confluence URL(s)")
    if _input_types > 1:
        raise ValueError("Provide only one type of input: text, file(s), or URL(s) — not a mix")
    opts = {
        "generate_new_tests": generate_new,
        "generate_p2_p3_tests": generate_p2_p3,
        "push_to_testrail": push_to_testrail,
        "target_section_id": target_section_id,
        "use_section_of_related": use_section_of_related,
    }
    return text, file_paths, confluence_urls, opts


@customer_bp.route('/requirement-analysis/runs', methods=['POST'])
def requirement_runs_start():
    """
    Start a requirement analysis in the background and return its History row (201).

    The run no longer lives inside the request: the page follows it over
    /runs/<session_id>/stream, reattaches after a refresh, and reopens it from History.
    Request body: same as POST /requirement-analysis (JSON or form).
    """
    try:
        text, file_paths, confluence_urls, opts = _requirement_analysis_params()
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if text:
        source = "Pasted text"
    elif file_paths:
        source = ", ".join(f.filename for f in request.files.getlist("file") if f.filename)
    else:
        extra = len(confluence_urls) - 1
        source = confluence_urls[0] + (f" +{extra} more" if extra else "")

    svc = RequirementAnalysisService(rag_service=current_app.config["RAG_SERVICE"])
    meta = requirement_runs.start(
        svc,
        user_id=session.get("user_id", "default"),
        text=text,
        file_paths=file_paths,
        confluence_urls=confluence_urls,
        opts=opts,
        source=source,
    )
    return jsonify(meta), 201


def _own_run(session_id):
    """The run's History row if it belongs to the caller. Anyone else gets a 404, not a 403."""
    meta = requirement_runs.get_meta(session_id)
    if not meta or meta.get("user_id") != session.get("user_id", "default"):
        return None
    return meta


def _run_not_found():
    return jsonify({"success": False, "error": "Run not found"}), 404


@customer_bp.route('/requirement-analysis/runs', methods=['GET'])
def requirement_runs_list():
    """The caller's runs, newest first: {items: [History row]}."""
    limit = max(1, min(request.args.get("limit", 20, type=int), 50))
    items = requirement_runs.list_runs(session.get("user_id", "default"), limit)
    return jsonify({"items": items}), 200


@customer_bp.route('/requirement-analysis/runs/<session_id>/events', methods=['GET'])
def requirement_runs_events(session_id):
    """Every recorded event of a run in one response, for replaying a past run."""
    if not _own_run(session_id):
        return _run_not_found()
    return jsonify({"events": requirement_runs.events(session_id)}), 200


@customer_bp.route('/requirement-analysis/runs/<session_id>/stream', methods=['GET'])
def requirement_runs_stream(session_id):
    """Server-Sent Events for a run: recorded events first, then live ones until it ends.

    Resumes after Last-Event-ID, which EventSource sends by itself when it reconnects.
    """
    if not _own_run(session_id):
        return _run_not_found()
    try:
        offset = int(request.headers.get("Last-Event-ID", "")) + 1
    except ValueError:
        offset = 0
    return Response(
        requirement_runs.stream(session_id, max(offset, 0)),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@customer_bp.route('/requirement-analysis/runs/<session_id>/cancel', methods=['POST'])
def requirement_runs_cancel(session_id):
    """Stop a running analysis. It ends as "cancelled" with whatever it analysed so far."""
    if not _own_run(session_id):
        return _run_not_found()
    if not requirement_runs.cancel(session_id):
        return jsonify({"success": False, "error": "Run is not running"}), 409
    return jsonify({"status": "cancelling", "session_id": session_id}), 200


@customer_bp.route('/requirement-analysis/suggest-case-update', methods=['POST'])
def requirement_analysis_suggest_case_update():
    """
    Get AI-suggested updated content for a test case that needs_update or partial.
    Body: testrail_id, requirement_text, suggested_changes (list), reason, current_title? (optional), current_content? (optional).
    If current_content is omitted, the case is fetched from TestRail.
    Returns: { success, title, steps, preconditions, expected_result, priority } or { success: false, error }.
    """
    try:
        data = request.get_json() or {}
        testrail_id = (data.get("testrail_id") or "").strip()
        requirement_text = (data.get("requirement_text") or "").strip()
        suggested_changes = data.get("suggested_changes")
        if not isinstance(suggested_changes, list):
            suggested_changes = []
        reason = (data.get("reason") or "").strip()
        if not testrail_id or not requirement_text:
            return jsonify({"success": False, "error": "testrail_id and requirement_text are required"}), 400
        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)
        current_title = (data.get("current_title") or "").strip() or None
        current_content = (data.get("current_content") or "").strip() or None
        result, err_msg = svc.suggest_case_update(
            testrail_id=testrail_id,
            requirement_text=requirement_text,
            suggested_changes=suggested_changes,
            reason=reason,
            current_title=current_title,
            current_content=current_content,
        )
        if result:
            return jsonify({
                "success": True,
                "title": result.get("title", ""),
                "steps": result.get("steps", ""),
                "preconditions": result.get("preconditions", ""),
                "expected_result": result.get("expected_result", ""),
                "priority": result.get("priority", "P2"),
            }), 200
        return jsonify({"success": False, "error": err_msg or "Could not generate suggestion"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _record_push(data, action, testrail_id, title):
    """Remember a push on the Requirements → Tests run it came from, when the page names one.

    Best-effort: the case already exists in TestRail, so a failure here must not turn
    the push into an error.
    """
    session_id = data.get("session_id")
    if not session_id:
        return
    try:
        requirement_runs.record_push(str(session_id), session.get("user_id", "default"),
                                     action, testrail_id, title, data.get("target"))
    except Exception as e:
        print(f"[{action}-case] could not record the push on run {session_id}: {e}")


@customer_bp.route('/requirement-analysis/update-case', methods=['POST'])
def requirement_analysis_update_case():
    """
    Update an existing test case in TestRail with user-provided (or AI-suggested then edited) content.
    Body: testrail_id, title, steps?, preconditions?, expected_result?, priority? (P0/P1/P2/P3).
    Returns: { success, testrail_id, error? }.
    """
    try:
        data = request.get_json() or {}
        testrail_id = (data.get("testrail_id") or "").strip()
        title = (data.get("title") or "").strip()
        if not testrail_id or not title:
            return jsonify({"success": False, "error": "testrail_id and title are required"}), 400
        steps = data.get("steps")
        preconditions = data.get("preconditions")
        expected_result = data.get("expected_result")
        priority = data.get("priority")
        if steps is not None:
            steps = (steps or "").strip() or None
        if preconditions is not None:
            preconditions = (preconditions or "").strip() or None
        if expected_result is not None:
            expected_result = (expected_result or "").strip() or None
        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)
        result = svc.update_case_in_testrail(
            testrail_id=testrail_id,
            title=title,
            steps=steps,
            expected_result=expected_result,
            preconditions=preconditions,
            priority=priority,
        )
        if result.get("success"):
            _record_push(data, "updated", testrail_id, title)
            # Re-ingest updated test into ChromaDB so the next analysis sees the new version
            try:
                ingested = svc.ingest_pushed_case_into_rag(
                    testrail_id=testrail_id,
                    title=title,
                    steps=steps,
                    expected_result=expected_result,
                    preconditions=preconditions,
                    priority=priority,
                )
                if ingested:
                    rag_svc = current_app.config.get("RAG_SERVICE")
                    if rag_svc and hasattr(rag_svc, "invalidate_vectorstore_for_reload"):
                        rag_svc.invalidate_vectorstore_for_reload()
            except Exception as e:
                import traceback
                print(f"[update-case] ChromaDB re-ingest failed: {e}")
                traceback.print_exc()
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/requirement-analysis/create-case', methods=['POST'])
def requirement_analysis_create_case():
    """
    Create a new test case in TestRail in the given section (AI-suggested or user-edited content).
    Body: section_id (int), title, steps?, preconditions?, expected_result?, priority? (P0/P1/P2/P3).
    Returns: { success, testrail_id, error? }.
    """
    try:
        data = request.get_json() or {}
        section_id = data.get("section_id")
        if section_id is not None:
            try:
                section_id = int(section_id)
            except (TypeError, ValueError):
                section_id = None
        title = (data.get("title") or "").strip()
        if not section_id or not title:
            return jsonify({"success": False, "error": "section_id and title are required"}), 400
        steps = data.get("steps")
        preconditions = data.get("preconditions")
        expected_result = data.get("expected_result")
        priority = data.get("priority")
        platform = (data.get("platform") or "").strip() or None
        requirement_text = (data.get("requirement_text") or "").strip() or None
        case_type = (data.get("case_type") or "").strip() or None
        if steps is not None:
            steps = (steps or "").strip() or None
        if preconditions is not None:
            preconditions = (preconditions or "").strip() or None
        if expected_result is not None:
            expected_result = (expected_result or "").strip() or None
        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)
        result = svc.create_case_in_testrail(
            section_id=section_id,
            title=title,
            steps=steps,
            expected_result=expected_result,
            preconditions=preconditions,
            priority=priority,
            requirement_text=requirement_text,
            platform=platform,
            case_type_name=case_type,
        )
        if result.get("success"):
            _record_push(data, "created", result.get("testrail_id", ""), title)
            # Ingest into ChromaDB so next requirement analysis sees it as an existing test
            try:
                ingested = svc.ingest_pushed_case_into_rag(
                    testrail_id=result.get("testrail_id", ""),
                    title=title,
                    steps=steps,
                    expected_result=expected_result,
                    preconditions=preconditions,
                    priority=priority,
                    requirement_text=requirement_text,
                    case_type=case_type,
                )
                if not ingested:
                    print(f"[create-case] ChromaDB ingest did not succeed for {result.get('testrail_id')} (check logs above)")
                else:
                    rag_svc = current_app.config.get("RAG_SERVICE")
                    if rag_svc and hasattr(rag_svc, "invalidate_vectorstore_for_reload"):
                        rag_svc.invalidate_vectorstore_for_reload()
            except Exception as e:
                import traceback
                print(f"[create-case] ChromaDB ingest failed: {e}")
                traceback.print_exc()
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/requirement-analysis/push', methods=['POST'])
def requirement_analysis_push():
    """
    Push user-selected generated tests to TestRail.
    Body: generated_tests (dict req_id -> list of test objects), related_tests (optional, for section resolution),
    use_section_of_related (bool), target_section_id (optional int).
    """
    try:
        data = request.get_json() or {}
        generated_tests = data.get("generated_tests") or {}
        related_tests = data.get("related_tests") or {}
        use_section_of_related = data.get("use_section_of_related", False)
        target_section_id = data.get("target_section_id")
        if target_section_id is not None:
            try:
                target_section_id = int(target_section_id)
            except (TypeError, ValueError):
                target_section_id = None
        if not generated_tests:
            return jsonify({"success": True, "pushed_to_testrail": [], "message": "No tests to push"}), 200
        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)
        pushed = svc.push_tests_to_testrail(
            generated_tests=generated_tests,
            related_tests=related_tests,
            use_section_of_related=use_section_of_related,
            target_section_id=target_section_id,
        )
        return jsonify({"success": True, "pushed_to_testrail": pushed}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- TestRail structure (for dynamic Project → Suite → Section in UI) ---

@customer_bp.route('/testrail/projects', methods=['GET'])
def testrail_projects():
    """List all TestRail projects (for dropdown)."""
    conn = _get_testrail_connector()
    if not conn:
        return jsonify({"success": False, "error": "TestRail not configured"}), 503
    try:
        projects = conn.get_projects()
        return jsonify({"success": True, "projects": projects}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/testrail/projects/<int:project_id>/suites', methods=['GET'])
def testrail_suites(project_id):
    """List test suites for a project."""
    conn = _get_testrail_connector()
    if not conn:
        return jsonify({"success": False, "error": "TestRail not configured"}), 503
    try:
        suites = conn.get_suites(project_id)
        return jsonify({"success": True, "suites": suites}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/testrail/projects/<int:project_id>/sections', methods=['GET'])
def testrail_sections(project_id):
    """List sections for a project. Query: suite_id (required for multi-suite projects)."""
    conn = _get_testrail_connector()
    if not conn:
        return jsonify({"success": False, "error": "TestRail not configured"}), 503
    suite_id = request.args.get("suite_id", type=int)
    try:
        sections = conn.get_sections(project_id, suite_id=suite_id)
        return jsonify({"success": True, "sections": sections}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/testrail/projects/<int:project_id>/suites', methods=['POST'])
def testrail_add_suite(project_id):
    """Create a new test suite. Body: { name, description? }."""
    conn = _get_testrail_connector()
    if not conn:
        return jsonify({"success": False, "error": "TestRail not configured"}), 503
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    try:
        suite = conn.add_suite(project_id, name, description=data.get("description") or None)
        return jsonify({"success": True, "suite": suite}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/testrail/projects/<int:project_id>/sections', methods=['POST'])
def testrail_add_section(project_id):
    """Create a new section. Body: { suite_id, name, parent_id? }."""
    conn = _get_testrail_connector()
    if not conn:
        return jsonify({"success": False, "error": "TestRail not configured"}), 503
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    suite_id = data.get("suite_id")
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    try:
        section = conn.add_section(
            project_id, name,
            suite_id=suite_id,
            parent_id=data.get("parent_id"),
            description=data.get("description") or None,
        )
        return jsonify({"success": True, "section": section}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@customer_bp.route('/testrail/unautomated-cases', methods=['GET'])
def testrail_unautomated_cases():
    """Return TestRail cases with Execution Mode == 'Pending Automation'."""
    project_id = request.args.get('project_id', type=int)
    suite_id = request.args.get('suite_id', type=int)
    section_id = request.args.get('section_id', type=int)

    if not project_id:
        return jsonify({'success': False, 'error': 'project_id required'}), 400

    conn = _get_testrail_connector()
    if not conn:
        return jsonify({'success': False, 'error': 'TestRail not configured'}), 503

    try:
        raw_cases = conn.get_test_cases(project_id, suite_id=suite_id)
        if not raw_cases:
            return jsonify({'success': True, 'cases': [], 'total_in_project': 0, 'pending_count': 0})

        custom_field_options = conn._build_custom_field_option_maps()

        # --- Dynamic field discovery ---
        # Field system_names differ between TestRail instances (custom_automation_type vs
        # custom_execution_mode, etc.), so we scan ALL option maps by label value instead of
        # assuming any particular field name.
        #
        # execution_mode field: the field whose options include "Automatable" AND "Manual"
        #   (the "Manual" co-presence distinguishes it from pure automation-status fields)
        # pending_automation fields: any field whose options include "Pending Automation"
        exec_mode_field = None   # system_name
        exec_mode_value = None   # numeric ID for "Automatable"
        pending_fields  = {}     # { system_name: numeric_id_for_"Pending Automation" }

        for field_name, option_map in custom_field_options.items():
            labels_lower = {v.strip().lower() for v in option_map.values()}
            for val_id, label in option_map.items():
                norm = label.strip().lower()
                if norm == 'automatable' and exec_mode_field is None:
                    # Prefer a field that also has "manual" (typical execution-mode field)
                    if 'manual' in labels_lower or exec_mode_field is None:
                        exec_mode_field = field_name
                        exec_mode_value = val_id
                if norm == 'pending automation':
                    pending_fields[field_name] = val_id

        def _field_contains(case: dict, field_name: str, target_id) -> bool:
            """Handle both scalar (int) and multi-select (list) custom field values."""
            val = case.get(field_name)
            if val is None or val == '' or val == []:
                return False
            if isinstance(val, list):
                return target_id in val
            return val == target_id

        def _matches(case: dict) -> bool:
            # execution_mode == 'Automatable' (scalar field; None/unset defaults to Automatable)
            if exec_mode_field is not None:
                raw = case.get(exec_mode_field)
                is_automatable = (raw == exec_mode_value) or (raw in (None, 0, ''))
                if not is_automatable:
                    return False
            # web/api/android/ios automation_status contains 'Pending Automation'
            if not pending_fields:
                return False
            return any(_field_contains(case, f, vid) for f, vid in pending_fields.items())

        filtered_raw = [c for c in raw_cases if _matches(c)]

        df_all = conn.transform_to_csv_format(
            raw_cases,
            case_type_map=None,
            custom_field_options=custom_field_options,
            section_id_to_label={},
        )
        pending_df = conn.transform_to_csv_format(
            filtered_raw,
            case_type_map=None,
            custom_field_options=custom_field_options,
            section_id_to_label={},
        ).copy() if filtered_raw else df_all.iloc[0:0].copy()

        if section_id:
            ids_in_section = {f"C{c.get('id')}" for c in raw_cases if c.get('section_id') == section_id}
            pending_df = pending_df[pending_df['ID'].isin(ids_in_section)]

        # Build a friendly label map for pending_fields: field_system_name -> human label
        # e.g. custom_web_automation_status_m -> "Web Automation", custom_api_automation_status_m -> "API Automation"
        _field_label_map = {
            'custom_web_automation_status_m': 'Web',
            'custom_api_automation_status_m': 'API',
            'custom_android_automation_status_m': 'Android',
            'custom_ios_automation_status_m': 'iOS',
        }

        # Build a lookup: raw case id -> dict of {display_label: resolved_value}
        # We resolve by checking the raw case field value against the option map
        raw_by_id = {c.get('id'): c for c in filtered_raw}

        pending_df_copy = pending_df[[
            'ID', 'Title', 'Priority', 'Type', 'Platform',
            'Steps', 'Preconditions', 'Expected Result', 'Section Hierarchy', 'Suite',
        ]].rename(columns={
            'Expected Result': 'expected_result',
            'Section Hierarchy': 'section',
        }).copy()

        def _automation_statuses(case_id_str):
            """Return list of {label, value} for all pending_fields that have a value for this case."""
            numeric_id = int(case_id_str.lstrip('C')) if case_id_str and case_id_str.lstrip('C').isdigit() else None
            raw = raw_by_id.get(numeric_id, {}) if numeric_id else {}
            result = []
            for field_name, _pending_id in pending_fields.items():
                raw_val = raw.get(field_name)
                if raw_val is None or raw_val == '' or raw_val == []:
                    continue
                option_map = custom_field_options.get(field_name, {})
                # Resolve label for each value (multi-select = list)
                vals = raw_val if isinstance(raw_val, list) else [raw_val]
                for v in vals:
                    resolved = option_map.get(v) or option_map.get(str(v)) or str(v)
                    display_key = _field_label_map.get(field_name) or field_name.replace('custom_', '').replace('_m', '').replace('_', ' ').title()
                    result.append({'label': display_key, 'value': resolved})
            return result

        cases = pending_df_copy.to_dict(orient='records')
        for c in cases:
            c['automation_statuses'] = _automation_statuses(c.get('ID', ''))

        return jsonify({
            'success': True,
            'cases': cases,
            'total_in_project': len(df_all),
            'pending_count': len(cases),
            '_debug': {
                'exec_mode_field': exec_mode_field,
                'exec_mode_value': exec_mode_value,
                'pending_fields': pending_fields,
                'all_custom_field_keys': list(custom_field_options.keys()),
            },
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@customer_bp.route('/testrail/improve-for-automation', methods=['POST'])
def testrail_improve_for_automation():
    """Use LLM to rewrite a TestRail test case to be clearer for automation."""
    data = request.get_json() or {}
    testrail_id = data.get('testrail_id', '')
    title = data.get('title', '')
    preconditions = data.get('preconditions', '')
    steps = data.get('steps', '')
    expected_result = data.get('expected_result', '')

    if not testrail_id and not title:
        return jsonify({'success': False, 'error': 'testrail_id or title required'}), 400

    try:
        svc = RequirementAnalysisService()
        result, error = svc.improve_for_automation(
            testrail_id=testrail_id,
            title=title,
            preconditions=preconditions,
            steps=steps,
            expected_result=expected_result,
        )
        if error:
            return jsonify({'success': False, 'error': error}), 500
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@customer_bp.route('/query', methods=['POST'])
def query():
    """Query the RAG system."""
    data = request.get_json()
    
    if not data or 'question' not in data:
        return jsonify({
            'success': False,
            'error': 'Question is required'
        }), 400
    
    question = data['question']
    session_id = data.get('session_id')
    bypass_cache = data.get('bypass_cache', False)  # Default to False if not provided
    use_rag = data.get('use_rag', True)  # Default to True (use RAG with documents)
    
    if not question or not question.strip():
        return jsonify({
            'success': False,
            'error': 'Question cannot be empty'
        }), 400
    
    # Get RAG service
    rag_service = current_app.config['RAG_SERVICE']
    
    # Process query.
    # session_id doubles as the cost-correlation id. The chat client does not
    # always send one, and without it the turn's records land as orphans and its
    # cost cannot be reported back — so mint a per-request id in that case.
    _correlation_id = session_id or f"ask-{uuid.uuid4().hex[:16]}"
    _t0 = time.time()
    result = rag_service.query(question, _correlation_id, bypass_cache=bypass_cache,
                               use_rag=use_rag)

    # Cost and time for this turn. session_id doubles as the correlation id, so
    # the cost records for one conversation group together.
    try:
        from backend.services.analytics_service import turn_metrics
        result.setdefault('metrics', turn_metrics(_correlation_id, since=_t0))
        result['metrics']['duration_s'] = round(time.time() - _t0, 2)
    except Exception:
        pass

    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 500

@customer_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'service': 'rag-customer-api'
    }), 200

