"""OCI credentials, quota aggregation, consent and power-action regressions."""

import hashlib
import importlib
import json
import types
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from test_guard import guard
from test_options_flow import load_flow

oracle = importlib.import_module("quota_guard.oracle_provider")

NOW = datetime(2026, 11, 14, tzinfo=UTC)
INSTANCE = "ocid1.instance.oc1.iad.aaaaexample"


def row(**changes):
    start, end = oracle.month_bounds(NOW)
    return types.SimpleNamespace(
        sku_part_number="B123",
        sku_name="Compute OCPU",
        service="Compute",
        unit="OCPU hours",
        computed_quantity=12,
        is_forecast=False,
        time_usage_started=start,
        time_usage_ended=end,
        **changes,
    )


class OracleTests(unittest.TestCase):
    def test_pem_multiline_flattened_and_escaped_with_matching_fingerprint(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        public = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        value = dict(
            tenancy="ocid1.tenancy.oc1..test",
            user="ocid1.user.oc1..test",
            region="il-jerusalem-1",
            fingerprint=hashlib.md5(public, usedforsecurity=False).hexdigest(),
        )
        for pasted in (
            pem,
            pem.replace("\n", ""),
            pem.replace("\n", "\\n"),
            pem + "OCI_API_KEY",
        ):
            clean = oracle.credentials(
                {
                    **value,
                    "private_key": pasted,
                    "key_file": "MUST_NOT_READ",
                    "security_token_file": "MUST_NOT_READ",
                }
            )
            self.assertEqual(clean["key_content"], pem)
            self.assertNotIn("key_file", clean)
            self.assertNotIn("security_token_file", clean)
        with self.assertRaises(ValueError):
            oracle.credentials({**value, "private_key": pem, "fingerprint": "00" * 16})
        for kind, prefix in (
            ("usage", "https://usageapi.il-jerusalem-1.oci.oraclecloud.com"),
            ("compute", "https://iaas.il-jerusalem-1.oraclecloud.com"),
        ):
            client = oracle._client(kind, clean)
            self.assertTrue(client.base_client.endpoint.startswith(prefix))
            client.base_client.session.close()

    def test_aggregate_keeps_skus_and_units_separate_and_validates_period(self):
        a, b, c = row(), row(), row()
        b.unit = "GB hours"
        c.sku_part_number = "B456"
        meters = oracle.aggregate([a, a, b, c], NOW)
        self.assertEqual(sorted(m["used"] for m in meters.values()), [12, 12, 24])
        for field, value in (
            ("computed_quantity", float("nan")),
            ("computed_quantity", -1),
            ("is_forecast", True),
            ("time_usage_ended", NOW),
            ("unit", None),
        ):
            bad = row()
            setattr(bad, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                oracle.aggregate([bad], NOW)

    def test_discovery_reads_all_pages_and_closes_client(self):
        client = Mock()
        client.request_summarized_usages.side_effect = [
            types.SimpleNamespace(
                data=types.SimpleNamespace(items=[row()]),
                headers={"opc-next-page": "p2"},
            ),
            types.SimpleNamespace(
                data=types.SimpleNamespace(items=[row()]), headers={}
            ),
        ]
        with patch.object(oracle, "_client", return_value=client):
            result = oracle.discover({"tenancy": "test-tenancy"}, NOW)
        self.assertEqual(next(iter(result.values()))["used"], 24)
        query = client.request_summarized_usages.call_args_list[0].args[0]
        self.assertEqual(
            query.group_by, ["service", "skuPartNumber", "skuName", "unit"]
        )
        self.assertEqual(query.query_type, "USAGE")
        self.assertEqual(
            client.request_summarized_usages.call_args.kwargs, {"page": "p2"}
        )
        client.base_client.session.close.assert_called_once()

    def test_missing_meter_is_unknown_not_zero_and_query_errors_are_sanitized(self):
        catalog = oracle.aggregate([row()], NOW)
        account = {
            "id": "own",
            "credentials": {"key_content": "private-secret"},
            "meters": list(catalog.values()),
        }
        provider = oracle.OracleProvider()
        with patch.object(oracle, "discover", return_value={}):
            result = provider.fetch(account, NOW)
        self.assertNotIn("sample", result[0])
        self.assertEqual(result[0]["reporting"]["state"], "no_activity_reported")
        self.assertIsNone(provider.fetch(account, NOW + timedelta(minutes=1)))
        failure = RuntimeError("private-secret")
        failure.status = 403
        with patch.object(oracle, "discover", side_effect=failure):
            result = provider.fetch(account, NOW + timedelta(minutes=16))
        self.assertEqual(result[0]["provider_error_code"], "permission_denied")
        self.assertNotIn("private-secret", json.dumps(result))

    def test_power_off_is_softstop_only_and_request_is_not_repeated(self):
        client = Mock()
        client.get_instance.return_value = types.SimpleNamespace(
            data=types.SimpleNamespace(id=INSTANCE, lifecycle_state="RUNNING"),
            headers={"etag": "version"},
        )
        with patch.object(oracle, "_client", return_value=client):
            first = oracle.stop_instances({}, [INSTANCE])
            second = oracle.stop_instances({}, [INSTANCE], first)
        self.assertEqual(first, {INSTANCE: "power_off_requested"})
        self.assertEqual(second, first)
        client.instance_action.assert_called_once_with(
            INSTANCE, "SOFTSTOP", if_match="version"
        )
        client.get_instance.return_value.data.lifecycle_state = "STOPPED"
        with patch.object(oracle, "_client", return_value=client):
            self.assertEqual(
                oracle.stop_instances({}, [INSTANCE], second), {INSTANCE: "stopped"}
            )
        self.assertEqual(client.instance_action.call_count, 1)

    def test_target_validation_rejects_wildcards_and_unexpected_resource(self):
        with patch.object(oracle, "_client") as client:
            for ids in ([], ["*"], [INSTANCE, INSTANCE], ["ocid1.volume.oc1.iad.aaaa"]):
                with self.assertRaises(ValueError):
                    oracle.validate_targets({}, ids)
            client.assert_not_called()


class OracleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_report_cannot_trigger_an_oracle_cutoff(self):
        with self.assertRaises(guard.vol.Invalid):
            await self.g.report(
                types.SimpleNamespace(
                    data={
                        "id": "oracle_test",
                        "used": 1000,
                        "limit": 100,
                        "timestamp": guard.dt_util.utcnow().isoformat(),
                        "period": "month",
                    }
                )
            )
        self.hass.async_add_executor_job.assert_not_awaited()

    async def asyncSetUp(self):
        self.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(
                async_entries=Mock(return_value=[]),
                async_get_entry=Mock(),
                async_set_disabled_by=AsyncMock(),
            ),
            data={},
            states={},
            bus=types.SimpleNamespace(async_fire=Mock()),
            async_add_executor_job=AsyncMock(),
        )
        entry = types.SimpleNamespace(
            data={
                "providers": {
                    "own": {
                        "type": "oracle",
                        "credentials": {"key_content": "secret"},
                        "meters": [],
                    }
                }
            }
        )
        self.g = guard.Guard(self.hass, entry)
        self.g.inventory_refresh = AsyncMock()
        self.g.provider_signals_refresh = AsyncMock()
        self.g.metric_diagnostics = Mock(return_value=[])
        self.cfg = dict(
            id="oracle_test",
            name="Oracle",
            provider_id="own",
            provider_kind="oracle",
            kind="free_allowance",
            source="Oracle API",
            unit="OCPU hours",
            max_age=1800,
            entry_ids=[],
            limit=100,
            warning_percent=70,
            cutoff_percent=80,
            enforce=False,
            oracle_instance_ids=[],
        )
        self.g.data["metrics"]["oracle_test"] = {
            "config": self.cfg,
            "sample": {
                "used": 85,
                "limit": 100,
                "period": "month",
                "timestamp": guard.dt_util.utcnow().isoformat(),
            },
        }

    async def test_custom_threshold_requires_consent_and_valid_allowance(self):
        await self.g.evaluate_metrics()
        self.hass.async_add_executor_job.assert_not_awaited()
        metric = self.g.data["metrics"]["oracle_test"]
        self.assertEqual(metric["status"], "cutoff")
        self.cfg.update(enforce=True, oracle_instance_ids=[INSTANCE])
        await self.g.evaluate_metrics()
        self.hass.async_add_executor_job.assert_not_awaited()
        metric["cutoff_authorized"] = True
        self.hass.async_add_executor_job.return_value = {
            INSTANCE: "power_off_requested"
        }
        await self.g.evaluate_metrics()
        self.assertIs(
            self.hass.async_add_executor_job.call_args.args[0], oracle.stop_instances
        )
        self.assertEqual(self.hass.async_add_executor_job.call_args.args[2], [INSTANCE])
        self.hass.config_entries.async_set_disabled_by.assert_not_awaited()
        self.assertNotIn("secret", json.dumps(self.g.snapshot()))

    async def test_unknown_stale_and_invalid_threshold_never_power_off(self):
        metric = self.g.data["metrics"]["oracle_test"]
        self.cfg.update(enforce=True, oracle_instance_ids=[INSTANCE])
        metric["cutoff_authorized"] = True
        for change in (
            {"used": None},
            {"timestamp": "2000-01-01T00:00:00+00:00"},
            {"limit": None},
        ):
            saved = dict(metric["sample"])
            metric["sample"].update(change)
            await self.g.evaluate_metrics()
            metric["sample"] = saved
        self.cfg["warning_percent"] = 90
        await self.g.evaluate_metrics()
        self.hass.async_add_executor_job.assert_not_awaited()

    async def test_configure_validates_targets_and_preserves_choices_on_poll(self):
        self.hass.async_add_executor_job.return_value = [
            {"id": INSTANCE, "name": "My instance", "region": "il-jerusalem-1"}
        ]
        cfg = {
            k: v
            for k, v in self.cfg.items()
            if k not in ("provider_id", "provider_kind")
        }
        cfg.update(
            limit=200,
            warning_percent=50,
            cutoff_percent=65,
            enforce=True,
            oracle_instance_ids=[INSTANCE],
        )
        await self.g.configure(types.SimpleNamespace(data=guard.SCHEMA(cfg)))
        self.assertIs(
            self.hass.async_add_executor_job.call_args.args[0], oracle.validate_targets
        )
        metric = self.g.data["metrics"]["oracle_test"]
        self.assertEqual(metric["percent"], 42.5)
        await self.g.apply_provider_row(
            {
                "config": {**self.cfg, "enforce": False},
                "provider_kind": "oracle",
                "sample": {
                    "used": 90,
                    "period": "month",
                    "timestamp": guard.dt_util.utcnow().isoformat(),
                },
            }
        )
        self.assertEqual(metric["config"]["cutoff_percent"], 65)
        self.assertTrue(metric["cutoff_authorized"])
        self.assertEqual(metric["sample"]["limit"], 200)

    async def test_native_power_off_screen_requires_target_confirmation(self):
        flow = load_flow().OptionsFlow()
        flow.hass = types.SimpleNamespace(data={"quota_guard": self.g})
        flow._source_id = "oracle_test"
        result = await flow.async_step_cutoff()
        self.assertEqual(result["step_id"], "oracle_cutoff")
        self.assertFalse(result["data_schema"]({})["enforce"])
        self.g.configure = AsyncMock()
        result = await flow.async_step_oracle_cutoff(
            {"enforce": True, "confirm_targets": False}
        )
        self.assertEqual(result["errors"]["base"], "oracle_confirmation_required")
        self.g.configure.assert_not_awaited()
        result = await flow.async_step_oracle_cutoff(
            {
                "enforce": True,
                "confirm_targets": True,
                "limit": 100,
                "warning_percent": 60,
                "cutoff_percent": 75,
                "oracle_instance_ids": [INSTANCE],
            }
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.g.configure.call_args.args[0].data["cutoff_percent"], 75)

    async def test_native_connect_only_saves_selected_meters_and_own_credentials(self):
        flow = load_flow().OptionsFlow()
        catalog = oracle.aggregate([row()], NOW)
        self.hass.data["quota_guard"] = self.g
        flow.hass = self.hass
        flow.config_entry = types.SimpleNamespace(data={})
        self.hass.config_entries.async_update_entry = Mock()
        self.hass.async_add_executor_job.side_effect = [
            {"key_content": "own-private"},
            catalog,
        ]
        form = await flow.async_step_oracle(
            {"name": "My Oracle", "private_key": "own-private"}
        )
        self.assertEqual(form["step_id"], "oracle_meters")
        self.assertNotIn("own-private", str(form))
        result = await flow.async_step_oracle_meters({"meter_ids": list(catalog)})
        self.assertEqual(result["type"], "create_entry")
        provider = next(
            iter(
                self.hass.config_entries.async_update_entry.call_args.kwargs["data"][
                    "providers"
                ].values()
            )
        )
        self.assertEqual(provider["credentials"]["key_content"], "own-private")
        self.assertEqual(provider["meters"][0]["id"], next(iter(catalog)))
        self.assertNotIn("used", provider["meters"][0])
        self.assertNotIn("enforce", provider)
        self.hass.config_entries.async_entries.assert_not_called()
