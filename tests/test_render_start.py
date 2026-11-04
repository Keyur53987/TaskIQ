import pytest
from scripts.render_start import command


def test_render_port_is_respected(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    argv = command()
    assert argv[argv.index("--port") + 1] == "10000"
    assert argv[argv.index("--host") + 1] == "0.0.0.0"


@pytest.mark.parametrize("value", ["0", "65536", "invalid"])
def test_invalid_port_is_rejected(monkeypatch, value):
    monkeypatch.setenv("PORT", value)
    with pytest.raises(ValueError):
        command()
