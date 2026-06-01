from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core.alembic_db_target import resolve_database_target, resolve_database_url


class _FakeUrl:
    def __init__(self, rendered: str):
        self._rendered = rendered

    def render_as_string(self, hide_password: bool = False) -> str:
        return self._rendered


class _FakeEngine:
    def __init__(self, rendered: str):
        self.url = _FakeUrl(rendered)


class TestAlembicDbTarget(unittest.TestCase):
    def test_default_target_is_central(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual("central", resolve_database_target({}))

    def test_central_target_override(self):
        self.assertEqual("central", resolve_database_target({"db_target": "central"}))

    def test_invalid_target_raises_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported Alembic db target 'tenant'"):
            resolve_database_target({"db_target": "tenant"})

    @patch("app.core.alembic_db_target._get_central_async_engine")
    @patch("app.core.alembic_db_target._get_main_async_engine")
    def test_default_url_uses_central_engine(self, mock_main_engine, mock_central_engine):
        mock_main_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_TickleRight_9210?charset=utf8mb4")
        mock_central_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_central?charset=utf8mb4")

        url = resolve_database_url({})

        self.assertIn("/pf_central", url)
        mock_central_engine.assert_called_once_with()
        mock_main_engine.assert_not_called()

    @patch("app.core.alembic_db_target._get_central_async_engine")
    @patch("app.core.alembic_db_target._get_main_async_engine")
    def test_central_url_override_uses_central_engine(self, mock_main_engine, mock_central_engine):
        mock_main_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_TickleRight_9210?charset=utf8mb4")
        mock_central_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_central?charset=utf8mb4")

        url = resolve_database_url({"db_target": "central"})

        self.assertIn("/pf_central", url)
        mock_central_engine.assert_called_once_with()
        mock_main_engine.assert_not_called()

    @patch("app.core.alembic_db_target._get_central_async_engine")
    @patch("app.core.alembic_db_target._get_main_async_engine")
    def test_main_url_override_uses_main_engine(self, mock_main_engine, mock_central_engine):
        mock_main_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_TickleRight_9210?charset=utf8mb4")
        mock_central_engine.return_value = _FakeEngine("mysql+aiomysql://user:pw@host:3306/pf_central?charset=utf8mb4")

        url = resolve_database_url({"db_target": "main"})

        self.assertIn("/pf_TickleRight_9210", url)
        mock_main_engine.assert_called_once_with()
        mock_central_engine.assert_not_called()
