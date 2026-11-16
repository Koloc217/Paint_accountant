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


def test_first_start_creates_the_data_folder(tmp_path):
    from paint_accountant.app import create_app

    s = load_settings(data_dir=tmp_path / "fresh")
    create_app(s)
    assert s.db_path.is_file()
    assert [p.name for p in s.data_dir.iterdir() if not p.name.startswith("paints.sqlite3")] == []


def test_config_file_created_from_example_once(tmp_path):
    from paint_accountant.config import ensure_config_file

    example = tmp_path / "config.example.ini"
    example.write_text("[server]\nport = 8642\n", encoding="utf-8")
    target = tmp_path / "config.ini"

    assert ensure_config_file(target, example) is True
    assert target.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")

    target.write_text("[server]\nport = 9999\n", encoding="utf-8")
    assert ensure_config_file(target, example) is False  # never overwrites user's file
    assert "9999" in target.read_text(encoding="utf-8")


def test_example_config_matches_defaults(monkeypatch):
    _clear_env(monkeypatch)
    from paint_accountant.config import CONFIG_EXAMPLE, DEFAULT_DATA_DIR, DEFAULT_HOST, DEFAULT_PORT

    s = load_settings(config_file=CONFIG_EXAMPLE, data_dir=None)
    assert (s.host, s.port) == (DEFAULT_HOST, DEFAULT_PORT)
    assert s.data_dir == APP_DIR / DEFAULT_DATA_DIR


def test_bad_port_is_explained(monkeypatch, tmp_path, capsys):
    import pytest
    from paint_accountant.__main__ import main

    _clear_env(monkeypatch)
    ini = tmp_path / "config.ini"
    ini.write_text("[server]\nport = 80a\n", encoding="utf-8")
    with pytest.raises(config.BadSetting, match=r"port in .*config\.ini is '80a'"):
        load_settings(config_file=ini)
    monkeypatch.setenv("PAINT_PORT", "abc")
    with pytest.raises(config.BadSetting, match="PAINT_PORT environment variable is 'abc'"):
        load_settings(config_file=ini)
    for bad in (0, 70000, -5):
        with pytest.raises(config.BadSetting, match="--port argument"):
            load_settings(port=bad, config_file=ini)
    assert load_settings(port=9200, config_file=ini).port == 9200  # a good argument wins over a bad file

    # from the command line: one plain line, no traceback
    with pytest.raises(SystemExit) as exit_:
        main(["--data-dir", str(tmp_path / "d"), "--config", str(ini)])
    assert "Can't start" in str(exit_.value) and "PAINT_PORT" in str(exit_.value)
