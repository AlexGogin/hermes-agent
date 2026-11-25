"""Kimi Coding /model picker: the live /coding/v1/models listing is the complete truth.

Verified against the wire 2026-11-24: ``api.kimi.com/coding/v1/models`` returns exactly
``[kimi-for-coding, kimi-for-coding-highspeed, k3, k3-256k, k3.1-pro, k3.1-pro-highspeed]``.
The curated list previously led the merge and surfaced retired ids (``kimi-k3``,
``kimi-k2.7-code``, …) the endpoint no longer serves — /model offered models that 400 on
first use, and the vendor-spelled ``k3.1-pro`` (dot, no ``kimi-`` prefix) was missing.
``kimi-coding`` is in ``_LIVE_ONLY_PICKER_PROVIDERS``: when the live fetch succeeds the
picker shows the live ids only; the curated row serves solely as the outage fallback.
"""

from unittest.mock import patch

import pytest

from providers import get_provider_profile
from hermes_cli.models import merge_profile_catalog, provider_model_ids
from hermes_cli.models_catalog_static import (
    _LIVE_ONLY_PICKER_PROVIDERS,
    _PROVIDER_MODELS,
)

# Exact ids the endpoint serves today (2026-11-24).
WIRE_IDS = {"kimi-for-coding", "kimi-for-coding-highspeed", "k3", "k3-256k", "k3.1-pro", "k3.1-pro-highspeed"}
# Picker priority order for the curated outage fallback: newest first.
CURATED_ORDER = ["k3.1-pro", "k3.1-pro-highspeed", "k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed"]

KIMI_CREDS = {
    "provider": "kimi-coding",
    "api_key": "sk-kimi-test",
    "base_url": "https://api.kimi.com/coding",
}


@pytest.fixture()
def hermetic_home(tmp_path):
    """Isolated HERMES_HOME so the picker cannot read a real disk cache row."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home = tmp_path / "kimi-home"
    home.mkdir()
    token = set_hermes_home_override(str(home))
    yield home
    reset_hermes_home_override(token)


class TestKimiCodingLiveOnlyPicker:
    def test_kimi_coding_is_live_only(self):
        assert "kimi-coding" in _LIVE_ONLY_PICKER_PROVIDERS

    def test_curated_catalog_matches_the_wire(self):
        """Curated fallback = exactly the wire ids, in picker-priority order (newest first)."""
        curated = list(_PROVIDER_MODELS["kimi-coding"])
        assert set(curated) == WIRE_IDS
        assert curated == CURATED_ORDER
        assert "kimi-k3" not in curated
        assert "kimi-k2.7-code" not in curated

    def test_live_listing_not_padded_with_curated(self):
        """A live response is served as-is: no curated ids the endpoint dropped."""
        profile = get_provider_profile("kimi-coding")
        live = ["kimi-for-coding", "k3", "k3.1-pro"]
        rows = merge_profile_catalog("kimi-coding", profile, list(live))
        assert list(rows) == live
        assert "kimi-k3" not in rows

    def test_picker_end_to_end_live_only(self, hermetic_home):
        """provider_model_ids: live ids only, retired curated ids gone, k3.1-pro present."""
        profile = get_provider_profile("kimi-coding")
        live = ["kimi-for-coding", "k3", "k3.1-pro"]
        with patch(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            return_value=dict(KIMI_CREDS),
        ), patch.object(type(profile), "fetch_models", return_value=list(live)):
            ids = provider_model_ids("kimi-coding", force_refresh=True)
        assert list(ids) == live
        assert "k3.1-pro" in ids
        assert "kimi-k3" not in ids

    def test_unreachable_endpoint_shows_no_models(self, hermetic_home):
        """CEO rule: only actually-available models are shown. An unreachable endpoint means
        nothing is verifiably available — the picker shows an EMPTY list, not the curated
        memory row that would 400 on first use."""
        profile = get_provider_profile("kimi-coding")
        # merge level: declined live -> empty list (not None, so no static-catalog fallback)
        assert merge_profile_catalog("kimi-coding", profile, None) == []
        # picker level: same, end to end
        with patch(
            "hermes_cli.auth.resolve_api_key_provider_credentials",
            return_value=dict(KIMI_CREDS),
        ), patch.object(type(profile), "fetch_models", return_value=None):
            ids = provider_model_ids("kimi-coding", force_refresh=True)
        assert list(ids) == []
