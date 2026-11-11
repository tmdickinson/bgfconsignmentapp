from bgf.settings import load_env_file


def test_env_file_fills_unset_variables_only(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nBGF_T_PLAIN=one\nBGF_T_QUOTED=\"two words\"\nBGF_T_SINGLE='a=b'\n"
        "BGF_T_SET=from-file\nnot a setting\n"
    )
    monkeypatch.setenv("BGF_T_SET", "already-set")
    for name in ("BGF_T_PLAIN", "BGF_T_QUOTED", "BGF_T_SINGLE"):
        monkeypatch.delenv(name, raising=False)
    load_env_file(path)

    import os
    assert os.environ["BGF_T_PLAIN"] == "one"
    assert os.environ["BGF_T_QUOTED"] == "two words"
    assert os.environ["BGF_T_SINGLE"] == "a=b"
    assert os.environ["BGF_T_SET"] == "already-set"
    for name in ("BGF_T_PLAIN", "BGF_T_QUOTED", "BGF_T_SINGLE"):
        monkeypatch.delenv(name)


def test_missing_env_file_is_ignored(tmp_path):
    load_env_file(tmp_path / "nothing-here")
