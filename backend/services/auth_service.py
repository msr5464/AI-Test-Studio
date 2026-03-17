"""
Authentication Service
======================
Service for user authentication and session management.
"""

from flask import session
from typing import Optional, Dict
from backend.models.user import User, UserStorage


class AuthService:
    """Service for authentication operations."""
    
    def __init__(self, user_storage: Optional[UserStorage] = None):
        """
        Initialize authentication service.
        
        Args:
            user_storage: User storage instance (creates new if not provided)
        """
        self.user_storage = user_storage or UserStorage()
        self._ensure_default_admin()
    
    def _ensure_default_admin(self):
        """Ensure default admin user exists."""
        # Check if any admin users exist
        admin_users = self.user_storage.list_users(role='admin')
        if not admin_users:
            # Create default admin user
            default_username = 'admin'
            default_password = 'admin123'  # Should be changed on first login
            self.user_storage.create_user(
                username=default_username,
                password=default_password,
                role='admin'
            )
            print(f"✅ Created default admin user: {default_username} / {default_password}")
            print(f"⚠️  IMPORTANT: Change the default admin password after first login!")
    
    def login(self, username: str, password: str) -> Dict:
        """
        Authenticate user and create session.
        
        Args:
            username: Username
            password: Password
        
        Returns:
            Dict with success status and user info or error message
        """
        user = self.user_storage.get_user_by_username(username)
        
        if not user:
            return {
                'success': False,
                'error': 'Invalid username or password'
            }
        
        if not user.verify_password(password):
            return {
                'success': False,
                'error': 'Invalid username or password'
            }
        
        # Update last login
        user.update_last_login()
        self.user_storage.update_user(user)
        
        # Store user info in session
        session['user_id'] = user.user_id
        session['username'] = user.username
        session['role'] = user.role
        session.permanent = True
        
        return {
            'success': True,
            'user': {
                'user_id': user.user_id,
                'username': user.username,
                'role': user.role
            }
        }
    
    def logout(self):
        """Logout current user."""
        session.clear()
        return {'success': True, 'message': 'Logged out successfully'}
    
    def get_current_user(self) -> Optional[User]:
        """
        Get current logged-in user.
        
        Returns:
            User object or None if not logged in
        """
        user_id = session.get('user_id')
        if user_id:
            return self.user_storage.get_user(user_id)
        return None
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return 'user_id' in session
    
    def is_admin(self) -> bool:
        """Check if current user is admin."""
        return session.get('role') == 'admin'
    
    def require_auth(self, admin_only: bool = False) -> Optional[Dict]:
        """
        Check authentication and optionally admin role.
        
        Args:
            admin_only: If True, require admin role
        
        Returns:
            None if authenticated, error dict if not
        """
        if not self.is_authenticated():
            return {
                'success': False,
                'error': 'Authentication required',
                'code': 'UNAUTHORIZED'
            }
        
        if admin_only and not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required',
                'code': 'FORBIDDEN'
            }
        
        return None
    
    def create_user(self, username: str, password: str, role: str = 'customer') -> Dict:
        """
        Create a new user (admin only).
        
        Args:
            username: Username
            password: Password
            role: User role ('admin' or 'customer')
        
        Returns:
            Dict with success status and user info or error
        """
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required'
            }
        
        user = self.user_storage.create_user(username, password, role)
        if user:
            return {
                'success': True,
                'user': {
                    'user_id': user.user_id,
                    'username': user.username,
                    'role': user.role
                }
            }
        else:
            return {
                'success': False,
                'error': 'Username already exists'
            }
    
    def list_users(self) -> Dict:
        """
        List all users (admin only).
        
        Returns:
            Dict with list of users
        """
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required'
            }
        
        users = self.user_storage.list_users()
        return {
            'success': True,
            'users': [
                {
                    'user_id': user.user_id,
                    'username': user.username,
                    'role': user.role,
                    'created_at': user.created_at,
                    'last_login': user.last_login
                }
                for user in users
            ],
            'count': len(users)
        }
    
    def change_password(self, old_password: str, new_password: str) -> Dict:
        """
        Change password for current user.
        
        Args:
            old_password: Current password
            new_password: New password
        
        Returns:
            Dict with success status or error
        """
        user = self.get_current_user()
        if not user:
            return {
                'success': False,
                'error': 'Not authenticated'
            }
        
        if not user.verify_password(old_password):
            return {
                'success': False,
                'error': 'Current password is incorrect'
            }
        
        # Update password (using pbkdf2:sha256 for Python 3.9 compatibility)
        from werkzeug.security import generate_password_hash
        user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        self.user_storage.update_user(user)
        
        return {
            'success': True,
            'message': 'Password changed successfully'
        }
    
    def update_user_role(self, user_id: str, new_role: str) -> Dict:
        """
        Update user role (admin only).
        
        Args:
            user_id: User ID to update
            new_role: New role ('admin' or 'customer')
        
        Returns:
            Dict with success status or error
        """
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required'
            }
        
        if new_role not in ['admin', 'customer']:
            return {
                'success': False,
                'error': 'Role must be "admin" or "customer"'
            }
        
        user = self.user_storage.get_user(user_id)
        if not user:
            return {
                'success': False,
                'error': 'User not found'
            }
        
        # Prevent removing last admin
        if user.role == 'admin' and new_role == 'customer':
            admin_users = self.user_storage.list_users(role='admin')
            if len(admin_users) <= 1:
                return {
                    'success': False,
                    'error': 'Cannot remove last admin user'
                }
        
        user.role = new_role
        self.user_storage.update_user(user)
        
        return {
            'success': True,
            'user': {
                'user_id': user.user_id,
                'username': user.username,
                'role': user.role
            },
            'message': f'User role updated to {new_role}'
        }
    
    def delete_user(self, user_id: str) -> Dict:
        """
        Delete a user (admin only).
        
        Args:
            user_id: User ID to delete
        
        Returns:
            Dict with success status or error
        """
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required'
            }
        
        user = self.user_storage.get_user(user_id)
        if not user:
            return {
                'success': False,
                'error': 'User not found'
            }
        
        # Prevent deleting last admin
        if user.role == 'admin':
            admin_users = self.user_storage.list_users(role='admin')
            if len(admin_users) <= 1:
                return {
                    'success': False,
                    'error': 'Cannot delete last admin user'
                }
        
        # Prevent deleting current user
        current_user = self.get_current_user()
        if current_user and current_user.user_id == user_id:
            return {
                'success': False,
                'error': 'Cannot delete your own account'
            }
        
        username = user.username
        self.user_storage.delete_user(user_id)
        
        return {
            'success': True,
            'message': f'User {username} deleted successfully'
        }
    
    def reset_user_password(self, user_id: str, new_password: str) -> Dict:
        """
        Reset password for any user (admin only).
        
        Args:
            user_id: User ID to reset password for
            new_password: New password
        
        Returns:
            Dict with success status or error
        """
        if not self.is_admin():
            return {
                'success': False,
                'error': 'Admin access required'
            }
        
        user = self.user_storage.get_user(user_id)
        if not user:
            return {
                'success': False,
                'error': 'User not found'
            }
        
        # Update password (using pbkdf2:sha256 for Python 3.9 compatibility)
        from werkzeug.security import generate_password_hash
        user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        self.user_storage.update_user(user)
        
        return {
            'success': True,
            'message': f'Password reset successfully for user {user.username}'
        }

