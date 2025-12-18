"""Base class for database stores with common session management."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from .database import SessionLocal


class BaseStore:
    """Base class providing common database session management.

    Use the _db_session context manager for automatic commit/rollback:

        with self._db_session() as session:
            # Do database operations
            session.add(...)

    The session will be committed on normal exit and rolled back on exception.
    """

    @contextmanager
    def _db_session(self) -> Generator[Session, None, None]:
        """Context manager for database sessions with auto-commit/rollback.

        Usage:
            with self._db_session() as session:
                session.add(some_object)
                # Auto-commits on exit, rolls back on exception
        """
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
