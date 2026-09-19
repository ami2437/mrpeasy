import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.dependencies import require_module, require_role
from app.models import Role, User
from app.routes.auth import (
    delete_user,
    list_users,
    register,
    reset_user_password,
    update_user,
)
from app.schemas import UserCreate, UserPasswordReset, UserUpdate
from app.services.auth import AuthService, RBACService, initialize_auth_roles


class AuthRBACTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "auth-test.db"
        self.engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        User.__table__.create(bind=self.engine)
        Role.__table__.create(bind=self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def add_user(self, username, role, active=True, password="temporary-password"):
        user = User(
            username=username,
            email=f"{username}@example.com",
            full_name=username.title(),
            hashed_password=AuthService.hash_password(password),
            role=role,
            is_active=active,
        )
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def test_role_matrix_matches_requested_module_access(self):
        super_admin = SimpleNamespace(role="super_admin")
        admin = SimpleNamespace(role="admin")
        employee = SimpleNamespace(role="employee")

        for module in ("batch_labels", "shipments", "invoicing", "customer_orders", "reports", "admin_ops", "auth"):
            self.assertTrue(RBACService.can_perform_action(super_admin, module))
        for module in ("batch_labels", "shipments", "invoicing", "customer_orders", "reports", "admin_ops"):
            self.assertTrue(RBACService.can_perform_action(admin, module))
        self.assertFalse(RBACService.can_perform_action(admin, "auth"))
        self.assertFalse(RBACService.can_perform_action(admin, "manage_users"))
        self.assertTrue(RBACService.can_perform_action(employee, "batch_labels"))
        self.assertTrue(RBACService.can_perform_action(employee, "shipments"))
        for module in ("invoicing", "customer_orders", "reports", "admin_ops", "auth"):
            self.assertFalse(RBACService.can_perform_action(employee, module))

    def test_dependencies_enforce_roles_and_modules(self):
        super_admin = SimpleNamespace(role="super_admin")
        admin = SimpleNamespace(role="admin")
        employee = SimpleNamespace(role="employee")

        self.assertIs(self.run_async(require_role("super_admin")(current_user=super_admin)), super_admin)
        with self.assertRaises(HTTPException) as role_error:
            self.run_async(require_role("super_admin")(current_user=admin))
        self.assertEqual(role_error.exception.status_code, 403)

        self.assertIs(self.run_async(require_module("batch_labels")(current_user=employee)), employee)
        with self.assertRaises(HTTPException) as module_error:
            self.run_async(require_module("reports")(current_user=employee))
        self.assertEqual(module_error.exception.status_code, 403)

    def test_migration_seeds_roles_and_preserves_credentials(self):
        original_hash = "existing-password-hash"
        legacy_owner = User(
            username="legacy-owner",
            email="owner@example.com",
            hashed_password=original_hash,
            role="owner",
            is_active=True,
        )
        legacy_viewer = User(
            username="legacy-viewer",
            email="viewer@example.com",
            hashed_password="another-existing-hash",
            role="viewer",
            is_active=True,
        )
        self.session.add_all([legacy_owner, legacy_viewer])
        self.session.commit()

        initialize_auth_roles(self.session)
        initialize_auth_roles(self.session)

        self.session.refresh(legacy_owner)
        self.session.refresh(legacy_viewer)
        self.assertEqual(legacy_owner.role, "super_admin")
        self.assertEqual(legacy_owner.hashed_password, original_hash)
        self.assertEqual(legacy_viewer.role, "employee")
        self.assertEqual(
            {role.name for role in self.session.query(Role).all()},
            {"employee", "admin", "super_admin"},
        )
        self.assertEqual(self.session.query(Role).count(), 3)

    def test_super_admin_can_create_update_and_reset_account(self):
        super_admin = self.add_user("root-user", "super_admin")
        created = self.run_async(
            register(
                UserCreate(
                    username="employee-one",
                    email="employee@example.com",
                    full_name="Employee One",
                    role="employee",
                    password="initial-password",
                ),
                current_user=super_admin,
                db=self.session,
            )
        )
        self.assertEqual(created.role, "employee")
        self.assertTrue(AuthService.verify_password("initial-password", created.hashed_password))

        updated = self.run_async(
            update_user(
                created.id,
                UserUpdate(username="admin-one", email="admin-one@example.com", full_name="Admin One", role="admin", is_active=True),
                current_user=super_admin,
                db=self.session,
            )
        )
        self.assertEqual(updated.username, "admin-one")
        self.assertEqual(updated.email, "admin-one@example.com")
        self.assertEqual(updated.role, "admin")
        self.assertEqual(updated.full_name, "Admin One")

        self.run_async(
            reset_user_password(
                created.id,
                UserPasswordReset(password="replacement-password"),
                current_user=super_admin,
                db=self.session,
            )
        )
        self.assertTrue(AuthService.verify_password("replacement-password", created.hashed_password))
        self.assertEqual(len(self.run_async(list_users(current_user=super_admin, db=self.session))), 2)

    def test_last_active_super_admin_cannot_be_demoted_deactivated_or_deleted(self):
        super_admin = self.add_user("only-root", "super_admin")

        with self.assertRaises(HTTPException):
            self.run_async(update_user(super_admin.id, UserUpdate(role="admin"), super_admin, self.session))
        with self.assertRaises(HTTPException):
            self.run_async(update_user(super_admin.id, UserUpdate(is_active=False), super_admin, self.session))
        with self.assertRaises(HTTPException):
            self.run_async(delete_user(super_admin.id, super_admin, self.session))

        self.assertEqual(self.session.query(User).filter(User.role == "super_admin", User.is_active.is_(True)).count(), 1)


if __name__ == "__main__":
    unittest.main()
