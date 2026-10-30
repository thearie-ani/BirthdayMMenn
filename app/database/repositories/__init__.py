"""
Database repositories package.
"""
from app.database.repositories.friend_repo import FriendRepository
from app.database.repositories.user_repo import UserRepository
from app.database.repositories.wish_repo import WishRepository

__all__ = ["FriendRepository", "UserRepository", "WishRepository"]
