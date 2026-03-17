"""
Customer API Routes
==================
Endpoints for customer operations (querying RAG system).
"""

import json
import os
import queue
import threading
from pathlib import Path
from werkzeug.utils import secure_filename
import tempfile

from flask import Blueprint, request, jsonify, current_app, Response, stream_with_context

from backend.services.requirement_analysis_service import RequirementAnalysisService

customer_bp = Blueprint('customer', __name__)

# Max lengths for streamed result to avoid huge SSE payloads that fail to send/parse (bigger docs)
# Test case content includes preconditions, steps, expected result — use enough to show full case
_TRIM_DESC = 6000
_TRIM_CONTENT = 12000
_TRIM_SPEC_CONTENT = 3000


def _trim_result_for_stream(result):
    """Return a copy of the result with long text fields truncated so the SSE payload stays manageable."""
    if not result or not isinstance(result, dict):
        return result
    out = dict(result)
    # Requirements: limit title/description
    if "requirements" in out and isinstance(out["requirements"], list):
        out["requirements"] = [
            {
                **r,
                "title": (r.get("title") or "")[:_TRIM_DESC],
                "description": (r.get("description") or "")[:_TRIM_DESC],
            }
            for r in out["requirements"]
        ]
    # Related specs: limit content
    if "related_specs" in out and isinstance(out["related_specs"], list):
        out["related_specs"] = [
            {**s, "content": ((s.get("content") or "")[:_TRIM_SPEC_CONTENT])}
            for s in out["related_specs"]
        ]
    # Related tests / tests_needing_update: limit content; keep preconditions, steps, expected_result separate (trim if present)
    for key in ("related_tests", "tests_needing_update"):
        if key not in out or not isinstance(out[key], dict):
            continue
        trimmed = {}
        for req_id, lst in out[key].items():
            if not isinstance(lst, list):
                trimmed[req_id] = lst
                continue
            trimmed[req_id] = []
            for t in lst:
                row = {**t, "content": ((t.get("content") or "")[:_TRIM_CONTENT])}
                if "preconditions" in t:
                    row["preconditions"] = (t.get("preconditions") or "")[:_TRIM_CONTENT]
                if "steps" in t:
                    row["steps"] = (t.get("steps") or "")[:_TRIM_CONTENT]
                if "expected_result" in t:
                    row["expected_result"] = (t.get("expected_result") or "")[:_TRIM_CONTENT]
                trimmed[req_id].append(row)
        out[key] = trimmed
    # Generated tests: limit steps, preconditions, expected_result
    if "generated_tests" in out and isinstance(out["generated_tests"], dict):
        trimmed_gt = {}
        for req_id, lst in out["generated_tests"].items():
            if not isinstance(lst, list):
                trimmed_gt[req_id] = lst
                continue
            trimmed_gt[req_id] = [
                {
                    **t,
                    "preconditions": ((t.get("preconditions") or "")[:_TRIM_CONTENT]),
                    "steps": ((t.get("steps") or "")[:_TRIM_CONTENT]),
                    "expected_result": ((t.get("expected_result") or "")[:_TRIM_CONTENT]),
                }
                for t in lst
            ]
        out["generated_tests"] = trimmed_gt
    return out


def _trim_requirement_result_for_stream(data):
    """Trim one requirement's result payload for streaming (keep size small)."""
    if not data or not isinstance(data, dict):
        return data
    out = dict(data)
    if "requirement" in out and isinstance(out["requirement"], dict):
        r = out["requirement"]
        out["requirement"] = {
            **r,
            "title": (r.get("title") or "")[:_TRIM_DESC],
            "description": (r.get("description") or "")[:_TRIM_DESC],
        }
    if "related_specs" in out and isinstance(out["related_specs"], list):
        out["related_specs"] = [
            {**s, "content": ((s.get("content") or "")[:_TRIM_SPEC_CONTENT])}
            for s in out["related_specs"]
        ]
    for key in ("related_tests", "tests_needing_update"):
        if key in out and isinstance(out[key], list):
            original_list = out[key]
            out[key] = []
            for t in original_list:
                row = {**t, "content": ((t.get("content") or "")[:_TRIM_CONTENT])}
                if "preconditions" in t:
                    row["preconditions"] = (t.get("preconditions") or "")[:_TRIM_CONTENT]
                if "steps" in t:
                    row["steps"] = (t.get("steps") or "")[:_TRIM_CONTENT]
                if "expected_result" in t:
                    row["expected_result"] = (t.get("expected_result") or "")[:_TRIM_CONTENT]
                out[key].append(row)
    if "generated_tests" in out and isinstance(out["generated_tests"], list):
        out["generated_tests"] = [
            {
                **t,
                "preconditions": ((t.get("preconditions") or "")[:_TRIM_CONTENT]),
                "steps": ((t.get("steps") or "")[:_TRIM_CONTENT]),
                "expected_result": ((t.get("expected_result") or "")[:_TRIM_CONTENT]),
            }
            for t in out["generated_tests"]
        ]
    return out


def _get_testrail_connector():
    """Build TestRail connector from config/env. Returns None if not configured."""
    from backend.rag.settings import get_config
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
    from backend.rag.settings import get_config
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
    try:
        text = None
        file_path = None
        confluence_url = None

        if request.is_json:
            data = request.get_json() or {}
            text = data.get("requirement_spec", "").strip() or None
            confluence_url = data.get("confluence_url", "").strip() or None
            generate_new = data.get("generate_new_tests", True)
            generate_p2_p3 = data.get("generate_p2_p3_tests", False)
            push_to_testrail = data.get("push_to_testrail", False)
            target_section_id = data.get("target_section_id")
            use_section_of_related = data.get("use_section_of_related", False)
        else:
            data = request.form
            text = (data.get("requirement_spec") or "").strip() or None
            confluence_url = (data.get("confluence_url") or "").strip() or None
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

        if "file" in request.files and request.files["file"].filename:
            f = request.files["file"]
            ext = Path(secure_filename(f.filename)).suffix.lower()
            if ext not in (".txt", ".pdf", ".docx", ".doc"):
                return jsonify({
                    "success": False,
                    "error": "Unsupported file type. Use .txt, .pdf, or .docx",
                }), 400
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                f.save(tmp.name)
                file_path = Path(tmp.name)

        input_count = sum(1 for x in (text, file_path, confluence_url) if x)
        if input_count == 0:
            return jsonify({
                "success": False,
                "error": "Provide exactly one of: requirement_spec (text), file, or confluence_url",
            }), 400
        if input_count > 1:
            return jsonify({
                "success": False,
                "error": "Provide only one input: requirement_spec, file, or confluence_url",
            }), 400

        rag_service = current_app.config["RAG_SERVICE"]
        svc = RequirementAnalysisService(rag_service=rag_service)

        result = svc.analyze(
            text=text,
            file_path=file_path,
            confluence_url=confluence_url,
            generate_new_tests=generate_new,
            generate_p2_p3_tests=generate_p2_p3,
            push_to_testrail=push_to_testrail,
            target_section_id=target_section_id,
            use_section_of_related=use_section_of_related,
        )

        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass

        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _requirement_analysis_params():
    """Parse requirement-analysis input and options from request. Returns (text, file_path, confluence_url, opts) or raises."""
    text = None
    file_path = None
    confluence_url = None
    if request.is_json:
        data = request.get_json() or {}
        text = data.get("requirement_spec", "").strip() or None
        confluence_url = data.get("confluence_url", "").strip() or None
        generate_new = data.get("generate_new_tests", True)
        generate_p2_p3 = data.get("generate_p2_p3_tests", False)
        push_to_testrail = data.get("push_to_testrail", False)
        target_section_id = data.get("target_section_id")
        use_section_of_related = data.get("use_section_of_related", False)
    else:
        data = request.form
        text = (data.get("requirement_spec") or "").strip() or None
        confluence_url = (data.get("confluence_url") or "").strip() or None
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
    if "file" in request.files and request.files["file"].filename:
        f = request.files["file"]
        ext = Path(secure_filename(f.filename)).suffix.lower()
        if ext not in (".txt", ".pdf", ".docx", ".doc"):
            raise ValueError("Unsupported file type. Use .txt, .pdf, or .docx")
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            f.save(tmp.name)
            file_path = Path(tmp.name)
    input_count = sum(1 for x in (text, file_path, confluence_url) if x)
    if input_count == 0:
        raise ValueError("Provide exactly one of: requirement_spec (text), file, or confluence_url")
    if input_count > 1:
        raise ValueError("Provide only one input: requirement_spec, file, or confluence_url")
    opts = {
        "generate_new_tests": generate_new,
        "generate_p2_p3_tests": generate_p2_p3,
        "push_to_testrail": push_to_testrail,
        "target_section_id": target_section_id,
        "use_section_of_related": use_section_of_related,
    }
    return text, file_path, confluence_url, opts


@customer_bp.route('/requirement-analysis/stream', methods=['POST'])
def requirement_analysis_stream():
    """
    Same as requirement-analysis but streams Server-Sent Events: progress (stage, message, progress 0-1) then result or error.
    Request body: same as POST /requirement-analysis (JSON or form).
    """
    try:
        text, file_path, confluence_url, opts = _requirement_analysis_params()
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    q = queue.Queue()
    rag_service = current_app.config["RAG_SERVICE"]
    svc = RequirementAnalysisService(rag_service=rag_service)

    def run_analyze():
        try:
            def progress_cb(stage, message, progress):
                q.put(("progress", stage, message, progress))

            def requirement_result_cb(req_id, data):
                q.put(("requirement_result", req_id, _trim_requirement_result_for_stream(data)))

            def doc_summary_cb(meta):
                q.put(("doc_summary", meta))

            result = svc.analyze(
                text=text,
                file_path=file_path,
                confluence_url=confluence_url,
                progress_callback=progress_cb,
                requirement_result_callback=requirement_result_cb,
                doc_summary_callback=doc_summary_cb,
                **opts,
            )
            q.put(("result", result))
        except Exception as e:
            q.put(("error", str(e)))
        finally:
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

    def sse(data_str):
        return f"data: {data_str}\n\n"

    def gen():
        # Send config immediately so frontend can apply correct thresholds before per-requirement blocks arrive
        try:
            _cov_min_sim = 60.0
            _v = os.getenv("REQUIREMENT_COVERAGE_SUFFICIENT_MIN_SIMILARITY", "").strip()
            if _v:
                _cov_min_sim = max(0.0, min(100.0, float(_v)))
        except (ValueError, TypeError):
            _cov_min_sim = 60.0
        try:
            _retrieval_threshold = 45.0
            _v = os.getenv("REQUIREMENT_RETRIEVAL_SIMILARITY_THRESHOLD", "").strip()
            if _v:
                _retrieval_threshold = max(0.0, min(100.0, float(_v)))
        except (ValueError, TypeError):
            _retrieval_threshold = 45.0
        yield sse(json.dumps({"type": "config", "coverage_min_similarity": _cov_min_sim, "retrieval_similarity_threshold": _retrieval_threshold}))

        thread = threading.Thread(target=run_analyze)
        thread.start()
        while True:
            try:
                item = q.get(timeout=300)
            except queue.Empty:
                break
            if item[0] == "doc_summary":
                try:
                    yield sse(json.dumps({"type": "doc_summary", "data": item[1]}, default=str))
                except Exception:
                    pass
                continue
            if item[0] == "requirement_result":
                try:
                    yield sse(
                        json.dumps(
                            {"type": "requirement_result", "req_id": item[1], "data": item[2]},
                            default=str,
                        )
                    )
                except Exception:
                    pass
                continue
            if item[0] == "result":
                try:
                    payload = _trim_result_for_stream(item[1])
                    yield sse(json.dumps(payload, default=str))
                except Exception as serr:
                    yield sse(json.dumps({"success": False, "error": "Failed to serialize result: " + str(serr)}))
                break
            if item[0] == "error":
                yield sse(json.dumps({"success": False, "error": item[1]}))
                break
            _, stage, message, progress = item
            yield sse(json.dumps({"stage": stage, "message": message, "progress": progress}))

    return Response(
        stream_with_context(gen()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    
    # Process query
    result = rag_service.query(question, session_id, bypass_cache=bypass_cache, use_rag=use_rag)
    
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

