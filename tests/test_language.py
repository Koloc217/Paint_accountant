"""Interface language lives in config.ini ([ui] language) and reaches the page via <html data-lang>."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paint_accountant.app import create_app
from paint_accountant.config import CONFIG_EXAMPLE, load_settings, save_language

OLD_CONFIG = """; my settings
[server]
; my port
port = 9000
"""


def test_default_is_auto(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    assert load_settings(config_file=tmp_path / "none.ini").language == "auto"
    assert load_settings(config_file=CONFIG_EXAMPLE).language == "auto"


def test_save_into_old_config_keeps_everything(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    cfg = tmp_path / "config.ini"
    cfg.write_text(OLD_CONFIG, encoding="utf-8")
    save_language(cfg, "en")
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith(OLD_CONFIG)  # comments and port untouched
    assert "[ui]" in text and "language = en" in text
    s = load_settings(config_file=cfg)
    assert (s.language, s.port) == ("en", 9000)
    save_language(cfg, "ru")  # replaced, not added twice
    text = cfg.read_text(encoding="utf-8")
    assert text.count("language =") == 1 and "language = ru" in text


def test_save_into_ui_section_without_language(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    cfg = tmp_path / "config.ini"
    cfg.write_text("[ui]\n; nothing yet\n[server]\nport = 9000\n", encoding="utf-8")
    save_language(cfg, "en")
    assert load_settings(config_file=cfg).language == "en"
    assert load_settings(config_file=cfg).port == 9000


def test_bad_value_means_auto(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    cfg = tmp_path / "config.ini"
    cfg.write_text("[ui]\nlanguage = klingon\n", encoding="utf-8")
    assert load_settings(config_file=cfg).language == "auto"


def test_env_overrides_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.ini"
    cfg.write_text("[ui]\nlanguage = ru\n", encoding="utf-8")
    monkeypatch.setenv("PAINT_LANGUAGE", "en")
    assert load_settings(config_file=cfg).language == "en"


def test_settings_page_saves_to_config_for_every_device(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    cfg = tmp_path / "config.ini"
    cfg.write_text(OLD_CONFIG, encoding="utf-8")
    app = create_app(load_settings(data_dir=tmp_path / "data", config_file=cfg))
    with TestClient(app) as c:
        assert 'data-lang="auto"' in c.get("/").text
        r = c.put("/api/settings/language", json={"language": "en"})
        assert r.status_code == 200 and r.json()["language"] == "en"
        assert c.put("/api/settings/language", json={"language": "de"}).status_code == 422
        with TestClient(app) as phone:  # another device gets it too
            assert 'data-lang="en"' in phone.get("/").text
            assert phone.get("/api/info").json()["language"] == "en"
    assert "language = en" in cfg.read_text(encoding="utf-8")
    # and after a restart
    app2 = create_app(load_settings(data_dir=tmp_path / "data", config_file=cfg))
    with TestClient(app2) as c:
        assert 'data-lang="en"' in c.get("/").text


def test_language_change_needs_sign_in_when_on(tmp_path, monkeypatch):
    monkeypatch.delenv("PAINT_LANGUAGE", raising=False)
    cfg = tmp_path / "config.ini"
    app = create_app(load_settings(data_dir=tmp_path / "data", config_file=cfg))
    with TestClient(app) as c:
        c.post("/api/auth/password", json={"password": "kraska"})
        with TestClient(app) as stranger:
            assert stranger.put("/api/settings/language", json={"language": "en"}).status_code == 401
        assert c.put("/api/settings/language", json={"language": "en"}).status_code == 200
