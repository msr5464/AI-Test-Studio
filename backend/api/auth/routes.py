"""
Authentication API Routes
========================
Endpoints for user authentication (login, logout, user management).
"""

from flask import Blueprint, request, jsonify, session, current_app
from functools import wraps

auth_bp = Blueprint('auth', __name__)


def require_auth(admin_only: bool = False):
    """
    Decorator to require authentication.
    
    Args:
        admin_only: If True, require admin role
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            auth_service = current_app.config.get('AUTH_SERVICE')
            if not auth_service:
                return jsonify({
                    'success': False,
                    'error': 'Authentication service not configured'
                }), 500
            
            error = auth_service.require_auth(admin_only=admin_only)
            if error:
                return jsonify(error), 401 if error.get('code') == 'UNAUTHORIZED' else 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Brute force protection: track failed login attempts per IP
import time as _time
_login_attempts: dict = {}  # {ip: [timestamp, timestamp, ...]}
_LOGIN_MAX_ATTEMPTS = 10
_LOGIN_LOCKOUT_SECONDS = 900  # 15 minutes


@auth_bp.route('/login', methods=['POST'])
def login():
    """Login endpoint."""
    # Rate limit check
    _ip = request.remote_addr or 'unknown'
    _now = _time.time()
    _attempts = _login_attempts.get(_ip, [])
    # Prune old attempts
    _attempts = [t for t in _attempts if _now - t < _LOGIN_LOCKOUT_SECONDS]
    _login_attempts[_ip] = _attempts
    if len(_attempts) >= _LOGIN_MAX_ATTEMPTS:
        _wait = int(_LOGIN_LOCKOUT_SECONDS - (_now - _attempts[0]))
        return jsonify({
            'success': False,
            'error': f'Too many login attempts. Try again in {_wait} seconds.'
        }), 429

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({
            'success': False,
            'error': 'Username and password required'
        }), 400

    auth_service = current_app.config.get('AUTH_SERVICE')
    if not auth_service:
        return jsonify({
            'success': False,
            'error': 'Authentication service not configured'
        }), 500

    result = auth_service.login(username, password)
    if result['success']:
        # Clear failed attempts on success
        _login_attempts.pop(_ip, None)
        return jsonify(result), 200
    else:
        # Record failed attempt
        _login_attempts.setdefault(_ip, []).append(_now)
        return jsonify(result), 401


@auth_bp.route('/signup', methods=['POST'])
def signup():
    """Self-service signup endpoint."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({
            'success': False,
            'error': 'Username and password required'
        }), 400

    auth_service = current_app.config.get('AUTH_SERVICE')
    if not auth_service:
        return jsonify({'success': False, 'error': 'Auth service not configured'}), 500

    # Create as member, pending approval
    user = auth_service.user_storage.create_user(username, password, role='member')
    if not user:
        return jsonify({'success': False, 'error': 'Username already exists'}), 400
        
    session['user_id'] = user.user_id
    session['username'] = user.username
    session['role'] = user.role
    session.permanent = True
        
    return jsonify({
        'success': True,
        'status': 'pending_approval',
        'message': 'Account created, pending admin approval.'
    }), 201


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Logout endpoint."""
    auth_service = current_app.config.get('AUTH_SERVICE')
    if not auth_service:
        return jsonify({
            'success': False,
            'error': 'Authentication service not configured'
        }), 500
    
    result = auth_service.logout()
    return jsonify(result), 200


@auth_bp.route('/me', methods=['GET'])
def get_current_user():
    """Get current logged-in user info."""
    auth_service = current_app.config.get('AUTH_SERVICE')
    user = auth_service.get_current_user()
    
    if user:
        if user.status != 'active':
            return jsonify({
                'success': False,
                'status': user.status,
                'error': 'Account pending approval or suspended'
            }), 403
        return jsonify({
            'success': True,
            'user': {
                'user_id': user.user_id,
                'username': user.username,
                'role': user.role,
                'status': user.status
            }
        }), 200
    else:
        return jsonify({
            'success': False,
            'error': 'Not authenticated'
        }), 401


@auth_bp.route('/users', methods=['GET'])
@require_auth(admin_only=True)
def list_users():
    """List all users (admin only)."""
    auth_service = current_app.config.get('AUTH_SERVICE')
    return jsonify(auth_service.list_users()), 200


@auth_bp.route('/users', methods=['POST'])
@require_auth(admin_only=True)
def create_user():
    """Create a new user (admin only)."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'customer')
    
    if not username or not password:
        return jsonify({
            'success': False,
            'error': 'Username and password required'
        }), 400
    
    if role not in ['admin', 'customer']:
        return jsonify({
            'success': False,
            'error': 'Role must be "admin" or "customer"'
        }), 400
    
    auth_service = current_app.config.get('AUTH_SERVICE')
    result = auth_service.create_user(username, password, role)
    
    if result['success']:
        return jsonify(result), 201
    else:
        return jsonify(result), 400


@auth_bp.route('/change-password', methods=['POST'])
@require_auth()
def change_password():
    """Change password for current user."""
    data = request.get_json()
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    
    if not old_password or not new_password:
        return jsonify({
            'success': False,
            'error': 'Old password and new password required'
        }), 400
    
    auth_service = current_app.config.get('AUTH_SERVICE')
    result = auth_service.change_password(old_password, new_password)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@auth_bp.route('/users/<user_id>', methods=['PUT'])
@require_auth(admin_only=True)
def update_user(user_id):
    """Update user role or status (admin only)."""
    data = request.get_json()
    role = data.get('role')
    status = data.get('status')
    
    auth_service = current_app.config.get('AUTH_SERVICE')
    user = auth_service.user_storage.get_user(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
        
    role = role or user.role
    if role not in ['admin', 'customer', 'member']:
        return jsonify({'success': False, 'error': 'Role must be admin, customer, or member'}), 400
        
    status = status or user.status
    if status not in ['pending_approval', 'active', 'rejected', 'suspended']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    # Losing the last admin is unrecoverable, not merely inconvenient: on the
    # next boot _ensure_default_admin finds no admin, tries to create one named
    # 'admin', finds that username already taken, returns None, and silently
    # does nothing — leaving the system with no way to administer it. The
    # equivalent guard already existed in auth_service.update_user_role(), but
    # no route ever called that method.
    demoting = user.role == 'admin' and role != 'admin'
    deactivating = user.role == 'admin' and status != 'active'
    if demoting or deactivating:
        remaining = [u for u in auth_service.user_storage.list_users(role='admin')
                     if u.user_id != user_id and getattr(u, 'status', '') == 'active']
        if not remaining:
            return jsonify({
                'success': False,
                'error': 'Cannot remove the last active admin — promote another '
                         'user to admin first.'
            }), 400

    user.role = role
    user.status = status

    if status == 'active' and not getattr(user, 'approved_at', None):
        from datetime import datetime
        user.approved_at = datetime.now().isoformat()
        user.approved_by = session.get('user_id')

    auth_service.user_storage.update_user(user)
    return jsonify({'success': True}), 200


@auth_bp.route('/users/<user_id>', methods=['DELETE'])
@require_auth(admin_only=True)
def delete_user(user_id):
    """Delete a user (admin only)."""
    auth_service = current_app.config.get('AUTH_SERVICE')
    result = auth_service.delete_user(user_id)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@auth_bp.route('/users/<user_id>/reset-password', methods=['POST'])
@require_auth(admin_only=True)
def reset_user_password(user_id):
    """Reset password for any user (admin only)."""
    data = request.get_json()
    new_password = data.get('new_password')
    
    if not new_password:
        return jsonify({
            'success': False,
            'error': 'New password is required'
        }), 400
    
    auth_service = current_app.config.get('AUTH_SERVICE')
    result = auth_service.reset_user_password(user_id, new_password)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

