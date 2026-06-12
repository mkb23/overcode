"""Config init/show/path and doctor diagnostics (coverage matrix rows 1 & 14).

Note: user config resolves under HOME (~/.overcode/config.yaml), which the
harness isolates per test — it does NOT follow OVERCODE_DIR.
"""

import pytest

pytestmark = pytest.mark.timeout(120)


def test_doctor_passes_in_container(oc):
    result = oc.run("doctor", session_arg=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_config_init_show_path(oc):
    init = oc.run("config", "init", session_arg=False)
    assert init.returncode == 0, init.stdout + init.stderr
    assert oc.config_file.exists()

    path_result = oc.run("config", "path", session_arg=False)
    assert path_result.returncode == 0
    assert str(oc.config_file) in path_result.stdout

    show = oc.run("config", "show", session_arg=False)
    assert show.returncode == 0


def test_config_show_reads_populated_config(oc):
    # `config show` renders known sections only; pricing isn't rendered, so
    # assert it loads a populated config without erroring and names the file.
    oc.config_file.write_text(
        "pricing:\n  input: 100.0\n  output: 500.0\n"
        "  cache_write: 100.0\n  cache_read: 10.0\n"
        'default_standing_instructions: "carry on please"\n'
    )
    show = oc.run("config", "show", session_arg=False)
    assert show.returncode == 0
    assert "carry on" in show.stdout
