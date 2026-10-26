from __future__ import annotations

from paint_accountant import config
from paint_accountant.config import APP_DIR, load_settings


def _clear_env(monkeypatch):
    for name in ("PAINT_HOST", "PAINT_PORT", "PAINT_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_keep_data_inside_app_folder(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    s = load_settings(config_file=tmp_path / "missing.ini")
    assert s.port == 8642
    assert s.host == "0.0.0.0"
    assert s.data_dir == APP_DIR / "data"
    assert s.db_path == APP_DIR / "data" / "paints.sqlite3"
    assert s.photos_dir == APP_DIR / "data" / "photos"


def test_config_file_relative_and_absolute_paths(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nport = 9000\ndata_dir = my_data\n", encoding="utf-8")
    s = load_settings(config_file=ini)
    assert s.port == 9000
    assert s.data_dir == APP_DIR / "my_data"  # relative to the app folder, not the cwd

    ini.write_text(f"[server]\ndata_dir = {tmp_path / 'abs'}\n", encoding="utf-8")
    assert load_settings(config_file=ini).data_dir == tmp_path / "abs"


def test_priority_args_over_env_over_file(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nport = 9000\n", encoding="utf-8")
    monkeypatch.setenv("PAINT_PORT", "9100")
    assert load_settings(config_file=ini).port == 9100
    assert load_settings(port=9200, config_file=ini).port == 9200


def test_empty_values_fall_back_to_defaults(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nhost =\nport =\n", encoding="utf-8")
    s = load_settings(config_file=ini)
    assert (s.host, s.port) == (config.DEFAULT_HOST, config.DEFAULT_PORT)


def test_first_start_creates_data_and_photo_folders(tmp_path):
    from paint_accountant.app import create_app

    s = load_settings(data_dir=tmp_path / "fresh")
    create_app(s)
    assert s.db_path.is_file()
    assert s.photos_dir.is_dir()
