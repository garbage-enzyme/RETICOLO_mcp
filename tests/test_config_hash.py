"""Tests for canonical config_hash."""

from __future__ import annotations

from reticolo_mcp.config_hash import compute_config_hash


class TestConfigHash:
    def test_deterministic(self):
        kwargs = dict(
            schema_version="1", reticolo_version="V10",
            wls_um=[5.0, 5.1], D=[1.0], nn=[5, 5],
            textures=[1.0, 1.5 + 0.01j],
            profil={"heights": [0, 0.1, 0], "indices": [1, 2, 3]},
            polarization=1,
        )
        h1 = compute_config_hash(**kwargs)
        h2 = compute_config_hash(**kwargs)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_wl_produces_different_hash(self):
        base = dict(
            schema_version="1", reticolo_version="V10",
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            polarization=1,
        )
        h1 = compute_config_hash(**base)
        base["wls_um"] = [5.1]
        h2 = compute_config_hash(**base)
        assert h1 != h2

    def test_different_polarization_produces_different_hash(self):
        base = dict(
            schema_version="1", reticolo_version="V10",
            wls_um=[5.0], D=[1.0], nn=[5, 5],
            textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            polarization=1,
        )
        h1 = compute_config_hash(**base)
        base["polarization"] = -1
        h2 = compute_config_hash(**base)
        assert h1 != h2

    def test_wl_order_independent(self):
        h1 = compute_config_hash(
            schema_version="1", reticolo_version="V10",
            wls_um=[5.1, 5.0], D=[1.0], nn=[5, 5],
            textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            polarization=1,
        )
        h2 = compute_config_hash(
            schema_version="1", reticolo_version="V10",
            wls_um=[5.0, 5.1], D=[1.0], nn=[5, 5],
            textures=[1.0],
            profil={"heights": [0, 0], "indices": [1, 1]},
            polarization=1,
        )
        assert h1 == h2
