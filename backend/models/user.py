"""
User Model
==========
User data model and storage management.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash


class User:
    """User model for authentication."""
    
    def __init__(self, username: str, password_hash: str, role: str = 'customer', user_id: Optional[str] = None):
        """
        Initialize user.
        
        Args:
            username: Username
            password_hash: Hashed password
            role: User role ('admin' or 'customer')
            user_id: Unique user ID (auto-generated if not provided)
        """
        self.user_id = user_id or self._generate_user_id(username)
        self.username = username
        self.password_hash = password_hash
        self.role = role
        self.created_at = datetime.now().isoformat()
        self.last_login = None
    
    @staticmethod
    def _generate_user_id(username: str) -> str:
        """Generate unique user ID from username."""
        return hashlib.md5(username.encode()).hexdigest()[:12]
    
    def to_dict(self) -> Dict:
        """Convert user to dictionary."""
        return {
            'user_id': self.user_id,
            'username': self.username,
            'password_hash': self.password_hash,
            'role': self.role,
            'created_at': self.created_at,
            'last_login': self.last_login
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'User':
        """Create user from dictionary."""
        user = cls(
            username=data['username'],
            password_hash=data['password_hash'],
            role=data.get('role', 'customer'),
            user_id=data.get('user_id')
        )
        user.created_at = data.get('created_at', user.created_at)
        user.last_login = data.get('last_login')
        return user
    
    def verify_password(self, password: str) -> bool:
        """Verify password against hash."""
        return check_password_hash(self.password_hash, password)
    
    def update_last_login(self):
        """Update last login timestamp."""
        self.last_login = datetime.now().isoformat()


class UserStorage:
    """User storage management."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize user storage.
        
        Args:
            storage_path: Path to user storage file
        """
        if storage_path is None:
            storage_path = Path('storage') / 'users.json'
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._users: Dict[str, User] = {}
        self._load_users()
    
    def _load_users(self):
        """Load users from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r') as f:
                    data = json.load(f)
                    self._users = {
                        user_id: User.from_dict(user_data)
                        for user_id, user_data in data.items()
                    }
            except Exception as e:
                print(f"⚠️  Failed to load users: {e}")
                self._users = {}
        else:
            self._users = {}
    
    def _save_users(self):
        """Save users to storage."""
        try:
            data = {
                user_id: user.to_dict()
                for user_id, user in self._users.items()
            }
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save users: {e}")
    
    def create_user(self, username: str, password: str, role: str = 'customer') -> Optional[User]:
        """
        Create a new user.
        
        Args:
            username: Username
            password: Plain text password (will be hashed)
            role: User role ('admin' or 'customer')
        
        Returns:
            Created user or None if username already exists
        """
        # Check if username already exists
        if self.get_user_by_username(username):
            return None
        
        # Create user with hashed password (using pbkdf2:sha256 for Python 3.9 compatibility)
        password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        user = User(username=username, password_hash=password_hash, role=role)
        self._users[user.user_id] = user
        self._save_users()
        return user
    
    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self._users.get(user_id)
    
    def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username."""
        for user in self._users.values():
            if user.username == username:
                return user
        return None
    
    def update_user(self, user: User):
        """Update user in storage."""
        self._users[user.user_id] = user
        self._save_users()
    
    def delete_user(self, user_id: str) -> bool:
        """
        Delete user.
        
        Returns:
            True if deleted, False if not found
        """
        if user_id in self._users:
            del self._users[user_id]
            self._save_users()
            return True
        return False
    
    def list_users(self, role: Optional[str] = None) -> List[User]:
        """
        List all users, optionally filtered by role.
        
        Args:
            role: Filter by role ('admin' or 'customer')
        
        Returns:
            List of users
        """
        users = list(self._users.values())
        if role:
            users = [u for u in users if u.role == role]
        return users
    
    def user_count(self) -> int:
        """Get total number of users."""
        return len(self._users)

