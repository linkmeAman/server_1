import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.prism_guard import CallerContext
from app.modules.nl2sql.dependencies import require_nl2sql_access
from app.modules.nl2sql.router import nl2sql_client, router


class TestNl2SqlRoutes(unittest.TestCase):
    def setUp(self) -> None:
        self.client = self._make_client(is_super=False)

    def _make_client(self, *, is_super: bool) -> TestClient:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_nl2sql_access] = lambda: CallerContext(user_id=1, is_super=is_super)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_generate_sql_passes_cache_fields(self) -> None:
        payload = {
            "status": "ok",
            "sql": "SELECT 1",
            "warnings": [],
            "tables_used": ["invoice"],
            "matched_groups": ["billing"],
            "attempt_count": 1,
            "cache_hit": True,
            "cache_source": "db_exact",
        }

        with patch.object(nl2sql_client, "generate_sql", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post("/api/nl2sql/v1/generate-sql", json={"query": "show invoices", "top_k": 5})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["cache_hit"])
        self.assertEqual(body["data"]["cache_source"], "db_exact")
        self.assertEqual(mock_call.await_count, 1)

    def test_teach_route_forwards_payload(self) -> None:
        payload = {
            "learning_status": "saved_new",
            "message": "saved",
            "instruction_id": 9,
            "similar_instructions": [],
            "requires_confirmation": False,
            "confirmation_token": None,
        }

        with patch.object(nl2sql_client, "teach", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post(
                "/api/nl2sql/v1/teach",
                json={
                    "instruction_type": "term_mapping",
                    "content": "counselor means employee",
                    "tables_affected": ["employee"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["learning_status"], "saved_new")
        self.assertEqual(mock_call.await_count, 1)

    def test_teach_confirm_route_forwards_payload(self) -> None:
        payload = {
            "learning_status": "confirmed",
            "message": "confirmed",
            "instruction_id": 10,
            "similar_instructions": [],
            "requires_confirmation": False,
            "confirmation_token": None,
        }

        with patch.object(nl2sql_client, "teach_confirm", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post(
                "/api/nl2sql/v1/teach/confirm",
                json={
                    "confirmation_token": "token-1",
                    "action": "replace",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["learning_status"], "confirmed")
        self.assertEqual(mock_call.await_count, 1)

    def test_instructions_query_params_are_forwarded(self) -> None:
        payload = [
            {
                "id": 1,
                "instruction_type": "term_mapping",
                "content": "counselor means employee",
                "confidence_score": 1.0,
                "is_verified": True,
                "is_active": True,
                "tables_affected": ["employee"],
            }
        ]

        with patch.object(nl2sql_client, "list_instructions", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/instructions?instruction_type=term_mapping&active_only=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 1)
        self.assertEqual(mock_call.await_count, 1)

    def test_ingest_groups_passes_skipped_counts(self) -> None:
        payload = {
            "inserted": 1,
            "updated": 0,
            "skipped": 3,
            "source": "all groups",
            "failure_count": 0,
            "failed_groups": [],
        }

        with patch.object(nl2sql_client, "ingest_groups", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post("/api/nl2sql/v1/ingest/groups", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["skipped"], 3)
        self.assertEqual(mock_call.await_count, 1)

    def test_ingest_knowledge_passes_skipped_counts(self) -> None:
        payload = {
            "inserted": 4,
            "updated": 1,
            "skipped": 7,
            "source": "knowledge",
        }

        with patch.object(nl2sql_client, "ingest_knowledge", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post(
                "/api/nl2sql/v1/ingest/knowledge",
                json={"include_column_catalog": True, "include_sql_examples": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["skipped"], 7)
        self.assertEqual(mock_call.await_count, 1)

    def test_ingest_patterns_passes_embedded_and_skipped_counts(self) -> None:
        payload = {
            "inserted": 1,
            "updated": 0,
            "skipped": 2,
            "embedded": 1,
            "source": "learned_patterns",
        }

        with patch.object(nl2sql_client, "ingest_patterns", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post("/api/nl2sql/v1/ingest/patterns", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["embedded"], 1)
        self.assertEqual(body["data"]["skipped"], 2)
        self.assertEqual(mock_call.await_count, 1)

    def test_ingest_instructions_passes_embedded_and_skipped_counts(self) -> None:
        payload = {
            "inserted": 2,
            "updated": 1,
            "skipped": 4,
            "embedded": 3,
            "source": "user_instructions",
        }

        with patch.object(nl2sql_client, "ingest_instructions", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post("/api/nl2sql/v1/ingest/instructions", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["embedded"], 3)
        self.assertEqual(body["data"]["skipped"], 4)
        self.assertEqual(mock_call.await_count, 1)

    def test_request_id_is_forwarded_from_header(self) -> None:
        payload = {
            "status": "ok",
            "sql": "SELECT 1",
            "warnings": [],
            "tables_used": ["invoice"],
            "matched_groups": ["billing"],
            "attempt_count": 1,
            "cache_hit": False,
            "cache_source": "none",
        }

        with patch.object(nl2sql_client, "generate_sql", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post(
                "/api/nl2sql/v1/generate-sql",
                headers={"X-Request-ID": "req-123"},
                json={"query": "show invoices", "top_k": 5},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-ID"), "req-123")
        self.assertEqual(mock_call.await_args.kwargs["request_id"], "req-123")

    def test_health_runtime_route_returns_runtime_payload(self) -> None:
        payload = {
            "status": "ok",
            "mysql_target": {"status": "ok", "issues": []},
            "schema_assets": {"status": "ok", "issues": []},
        }

        with patch.object(nl2sql_client, "health_runtime", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/health/runtime")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["mysql_target"]["status"], "ok")
        self.assertEqual(mock_call.await_count, 1)

    def test_get_model_routing_route_returns_payload(self) -> None:
        payload = {
            "llm": {"provider": "openai", "model": "gpt-4o-mini"},
            "sql": {"provider": "ollama", "model": "sqlcoder"},
        }

        with patch.object(nl2sql_client, "get_model_routing", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/config/model-routing")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["sql"]["provider"], "ollama")
        self.assertEqual(mock_call.await_count, 1)

    def test_patch_model_routing_requires_super_access(self) -> None:
        response = self.client.patch(
            "/api/nl2sql/v1/config/model-routing",
            json={"sql_model_provider": "ollama", "sql_model": "sqlcoder"},
        )

        self.assertEqual(response.status_code, 403)

    def test_patch_model_routing_route_updates_runtime_config(self) -> None:
        payload = {
            "updated": True,
            "model_routing": {"sql": {"provider": "ollama", "model": "sqlcoder"}},
        }
        super_client = self._make_client(is_super=True)

        with patch.object(nl2sql_client, "patch_model_routing", AsyncMock(return_value=payload)) as mock_call:
            response = super_client.patch(
                "/api/nl2sql/v1/config/model-routing",
                json={
                    "sql_model_provider": "ollama",
                    "sql_model": "sqlcoder",
                    "startup_enforcement_mode": "warn",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["model_routing"]["sql"]["provider"], "ollama")
        self.assertEqual(mock_call.await_args.kwargs["request_data"].sql_model_provider, "ollama")

    def test_query_groups_route_forwards_payload(self) -> None:
        payload = {
            "matched_groups": ["billing"],
            "tables_in_scope": ["invoice"],
            "context": "Group: billing",
            "results": [],
        }

        with patch.object(nl2sql_client, "query_groups", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post(
                "/api/nl2sql/v1/query/groups",
                json={"query": "show unpaid invoices by counselor", "top_k": 3},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["matched_groups"], ["billing"])
        self.assertEqual(mock_call.await_count, 1)

    def test_pending_teach_route_forwards_query_params(self) -> None:
        payload = {
            "results": [
                {
                    "token": "abc123",
                    "instruction_type": "term_mapping",
                    "content": "counselor means employee",
                    "is_expired": False,
                }
            ],
            "stats": {"pending_active_count": 1},
        }

        with patch.object(
            nl2sql_client,
            "list_pending_teach_confirmations",
            AsyncMock(return_value=payload),
        ) as mock_call:
            response = self.client.get("/api/nl2sql/v1/teach/pending?limit=20&include_expired=false")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["data"]["stats"]["pending_active_count"], 1)
        self.assertEqual(mock_call.await_count, 1)

    def test_delete_instruction_route_forwards_path_param(self) -> None:
        payload = {"deactivated": True, "instruction_id": 7}

        with patch.object(nl2sql_client, "delete_instruction", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.delete("/api/nl2sql/v1/instructions/7")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["deactivated"])
        self.assertEqual(mock_call.await_args.kwargs["instruction_id"], 7)

    def test_cache_clear_route_returns_cache_counts(self) -> None:
        payload = {
            "embed_cleared": 1,
            "sql_cleared": 2,
            "semantic_sql_cleared": 3,
            "ask_cleared": 4,
            "db_query_cache_cleared": 5,
        }

        with patch.object(nl2sql_client, "cache_clear", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.post("/api/nl2sql/v1/cache/clear", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["db_query_cache_cleared"], 5)
        self.assertEqual(mock_call.await_count, 1)

    def test_benchmark_list_route_forwards_query_params(self) -> None:
        payload = {"results": [{"id": 1, "query": "show invoices"}]}

        with patch.object(nl2sql_client, "benchmark_list_cases", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/benchmark/cases?limit=25&active_only=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["results"][0]["id"], 1)
        self.assertEqual(mock_call.await_count, 1)

    def test_logs_days_route_returns_wrapped_payload(self) -> None:
        payload = {"log_dir": "logs", "results": [{"day": "current", "file": "nl2sql.log"}]}

        with patch.object(nl2sql_client, "logs_days", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/logs/days")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["results"][0]["day"], "current")
        self.assertEqual(mock_call.await_count, 1)

    def test_logs_recent_route_forwards_query_params(self) -> None:
        payload = {
            "day": "current",
            "file": "nl2sql.log",
            "path": "logs/nl2sql.log",
            "lines": ["one"],
            "total_lines_returned": 1,
        }

        with patch.object(nl2sql_client, "logs_recent", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/logs/recent?day=current&lines=25")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total_lines_returned"], 1)
        self.assertEqual(mock_call.await_args.kwargs["day"], "current")
        self.assertEqual(mock_call.await_args.kwargs["lines"], 25)

    def test_metrics_prometheus_route_preserves_text_response(self) -> None:
        payload = type("RawResponse", (), {"content": b"metric 1\n", "content_type": "text/plain; version=0.0.4"})()

        with patch.object(nl2sql_client, "metrics_prometheus", AsyncMock(return_value=payload)) as mock_call:
            response = self.client.get("/api/nl2sql/v1/metrics/prometheus")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "metric 1\n")
        self.assertIn("text/plain", response.headers["content-type"])
        self.assertEqual(mock_call.await_count, 1)

    def test_logs_stream_route_preserves_ndjson_stream(self) -> None:
        async def fake_stream():
            yield b"{\"event\":\"log_line\"}\n"

        with patch.object(nl2sql_client, "logs_stream", return_value=fake_stream()) as mock_call:
            response = self.client.get(
                "/api/nl2sql/v1/logs/stream?day=current&backlog=10&follow=true&poll_interval_ms=500"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-ndjson", response.headers["content-type"])
        self.assertIn("{\"event\":\"log_line\"}", response.text)
        self.assertEqual(mock_call.call_args.kwargs["backlog"], 10)
        self.assertEqual(mock_call.call_args.kwargs["poll_interval_ms"], 500)


if __name__ == "__main__":
    unittest.main()
