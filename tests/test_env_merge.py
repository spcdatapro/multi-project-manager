import pytest

from usr.plugins.multi_project_manager.helpers.env_merge import (
    apply_mode,
    get_keys,
    merge_env,
    replace_env,
)

SOURCE = 'API_URL="https://new.example"\nTIMEOUT="30"\n'
TARGET = 'API_URL="https://old.example"\nDB_NAME="cliente_a"\n'


def test_merge_overwrites_keeps_target_only_and_appends_new():
    assert merge_env(SOURCE, TARGET) == (
        'API_URL="https://new.example"\nDB_NAME="cliente_a"\nTIMEOUT="30"\n'
    )


def test_replace_makes_target_identical_to_source():
    assert replace_env(SOURCE) == SOURCE
    assert apply_mode("replace", SOURCE, TARGET) == SOURCE


def test_merge_preserves_target_comments_blanks_and_order():
    target = "# db settings\nDB_NAME=a\n\n# api\nAPI_URL=old\n"
    assert merge_env("API_URL=new\n", target) == (
        "# db settings\nDB_NAME=a\n\n# api\nAPI_URL=new\n"
    )


def test_merge_into_empty_target_and_empty_source():
    assert merge_env(SOURCE, "") == SOURCE
    assert merge_env("", TARGET) == TARGET
    assert merge_env("", "") == ""


def test_merge_is_idempotent():
    once = merge_env(SOURCE, TARGET)
    assert merge_env(SOURCE, once) == once


def test_merge_drops_later_duplicates_of_overwritten_key():
    assert merge_env("A=new\n", "A=1\nB=2\nA=3\n") == "A=new\nB=2\n"


def test_merge_source_duplicate_last_wins():
    assert merge_env("A=1\nA=2\n", "") == "A=2\n"


def test_multiline_quoted_value_survives():
    source = 'KEY="line1\nline2"\n'
    assert merge_env(source, "OTHER=x\n") == 'OTHER=x\nKEY="line1\nline2"\n'
    assert replace_env(source) == source


def test_unparsable_target_lines_are_kept():
    target = "this is not env\nA=1\n"
    assert merge_env("A=2\n", target) == "this is not env\nA=2\n"


def test_case_sensitivity_flag():
    assert merge_env("api_key=new\n", "API_KEY=old\n") == "API_KEY=old\napi_key=new\n"
    assert merge_env("api_key=new\n", "API_KEY=old\n", upper_keys=True) == "api_key=new\n"


def test_inline_comment_comes_from_source_line():
    assert merge_env("A=new # why\n", "A=old # stale\n") == "A=new # why\n"


def test_get_keys_dedupes_and_ignores_comments():
    assert get_keys("# c\nA=1\nB=2\nA=3\n") == ["A", "B"]
    assert get_keys("a=1\n", upper_keys=True) == ["A"]


def test_crlf_input_is_normalized_to_lf():
    assert merge_env("A=1\r\n", "B=2\r\n") == "B=2\nA=1\n"


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        apply_mode("overwrite", SOURCE, TARGET)
