"""
Unit tests for the `overcode parallelism` command (#365).
"""

import json
from unittest.mock import patch

from typer.testing import CliRunner

from overcode.cli import app
from overcode.cli.parallelism import _recommend_cap

runner = CliRunner()


class TestRecommendCap:
    def test_scales_with_cores(self):
        # 16 cores, plenty of RAM → CPU is the binding constraint
        # (16 - 1 reserved) / 0.5 = 30
        assert _recommend_cap(16, 128.0) == 30

    def test_scales_with_ram(self):
        # 16 cores but only 3 GB RAM → RAM is the binding constraint
        # 3 / 1.5 = 2
        assert _recommend_cap(16, 3.0) == 2

    def test_minimum_one(self):
        # Tiny machine — never recommend zero
        assert _recommend_cap(1, 0.0) == 1
        assert _recommend_cap(1, 0.5) == 1


class TestParallelismCommand:
    def test_help(self):
        result = runner.invoke(app, ["parallelism", "--help"])
        assert result.exit_code == 0
        assert "max-children" in result.output

    def test_json_output_shape(self):
        with patch("overcode.launcher.ClaudeLauncher") as ML:
            ML.return_value.list_sessions.return_value = []
            result = runner.invoke(app, ["parallelism", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert {"cores", "ram_gb", "recommended_max_children",
                "current_count", "headroom", "over_limit"} <= set(data)
        assert data["current_count"] == 0
        assert data["over_limit"] is False
