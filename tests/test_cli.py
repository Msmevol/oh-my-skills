"""
Tests for CLI module - adapted for subprocess execution
"""

import pytest
import os
import sys
import tempfile
import shutil
from unittest.mock import MagicMock, patch
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.cli import create_parser, cmd_list, cmd_run


@pytest.fixture
def mock_skill_dir():
    temp_dir = tempfile.mkdtemp()
    skill_path = os.path.join(temp_dir, ".opencode", "skills", "test-skill")
    os.makedirs(skill_path)

    skill_content = """---
name: test-skill
description: A test skill
---

# Test Skill

## Steps
1. Do something
"""
    with open(os.path.join(skill_path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_content)

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


class TestParser:
    def test_parser_with_skill_name(self):
        parser = create_parser()
        args = parser.parse_args(["my-skill"])
        assert args.skill_name == "my-skill"
        assert args.user_request is None

    def test_parser_with_request(self):
        parser = create_parser()
        args = parser.parse_args(["my-skill", "do something"])
        assert args.skill_name == "my-skill"
        assert args.user_request == "do something"

    def test_parser_list(self):
        parser = create_parser()
        args = parser.parse_args(["--list"])
        assert args.list is True

    def test_parser_format(self):
        parser = create_parser()
        args = parser.parse_args(["my-skill", "--format", "json"])
        assert args.format == "json"

    def test_parser_url(self):
        parser = create_parser()
        args = parser.parse_args(["my-skill", "--url", "http://example.com:8080"])
        assert args.url == "http://example.com:8080"

    def test_parser_defaults(self):
        parser = create_parser()
        args = parser.parse_args(["my-skill"])
        assert args.url == "http://localhost:4096"
        assert args.agent == "skill-executor"
        assert args.timeout == 600
        assert args.format == "text"


class TestCmdList:
    def test_list_skills_text_format(self, mock_skill_dir, capsys):
        from src.skill_loader import SkillLoader

        loader = SkillLoader(search_dirs=[mock_skill_dir])
        result = cmd_list(loader, "text")
        captured = capsys.readouterr()
        assert result == 0
        assert "test-skill" in captured.out
        assert "A test skill" in captured.out

    def test_list_skills_json_format(self, mock_skill_dir, capsys):
        from src.skill_loader import SkillLoader

        loader = SkillLoader(search_dirs=[mock_skill_dir])
        result = cmd_list(loader, "json")
        captured = capsys.readouterr()
        assert result == 0
        assert "test-skill" in captured.out
        assert "A test skill" in captured.out

    def test_list_no_skills(self, capsys):
        temp_dir = tempfile.mkdtemp()
        try:
            from src.skill_loader import SkillLoader

            loader = SkillLoader(search_dirs=[temp_dir])
            result = cmd_list(loader, "text")
            captured = capsys.readouterr()
            assert result == 0
            assert "No skills found" in captured.out
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestCmdRun:
    @patch("src.cli.OpenCodeClient")
    def test_run_success(self, mock_client_class, mock_skill_dir, capsys):
        mock_client = MagicMock()
        mock_client.health_check.return_value = True
        mock_client.run_skill.return_value = {
            "status": "success",
            "stdout": "Skill executed successfully",
            "stderr": "",
            "returncode": 0,
            "error": None,
        }
        mock_client_class.return_value = mock_client

        from src.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(
            ["test-skill", "do something", "--dir", mock_skill_dir]
        )

        result = cmd_run(args.skill_name, args.user_request, args)
        captured = capsys.readouterr()

        assert result == 0
        assert "success" in captured.out

    @patch("src.cli.OpenCodeClient")
    def test_run_server_not_connected(self, mock_client_class, capsys):
        mock_client = MagicMock()
        mock_client.health_check.return_value = False
        mock_client_class.return_value = mock_client

        from src.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["test-skill"])

        result = cmd_run(args.skill_name, args.user_request, args)
        captured = capsys.readouterr()

        assert result == 1
        assert "Cannot connect" in captured.out

    @patch("src.cli.OpenCodeClient")
    def test_run_skill_not_found(self, mock_client_class, capsys):
        mock_client = MagicMock()
        mock_client.health_check.return_value = True
        mock_client_class.return_value = mock_client

        from src.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["nonexistent-skill"])

        result = cmd_run(args.skill_name, args.user_request, args)
        captured = capsys.readouterr()

        assert result == 1
        assert "Skill not found" in captured.out

    @patch("src.cli.OpenCodeClient")
    def test_run_json_format(self, mock_client_class, mock_skill_dir, capsys):
        mock_client = MagicMock()
        mock_client.health_check.return_value = True
        mock_client.run_skill.return_value = {
            "status": "success",
            "stdout": "Skill executed successfully",
            "stderr": "",
            "returncode": 0,
            "error": None,
        }
        mock_client_class.return_value = mock_client

        from src.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(
            ["test-skill", "--format", "json", "--dir", mock_skill_dir]
        )

        result = cmd_run(args.skill_name, args.user_request, args)
        captured = capsys.readouterr()

        assert result == 0
        assert '"status": "success"' in captured.out

    @patch("src.cli.OpenCodeClient")
    def test_run_failed_execution(self, mock_client_class, mock_skill_dir, capsys):
        mock_client = MagicMock()
        mock_client.health_check.return_value = True
        mock_client.run_skill.return_value = {
            "status": "failed",
            "stdout": "",
            "stderr": "Error occurred",
            "returncode": 1,
            "error": "Execution failed",
        }
        mock_client_class.return_value = mock_client

        from src.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["test-skill", "--dir", mock_skill_dir])

        result = cmd_run(args.skill_name, args.user_request, args)
        captured = capsys.readouterr()

        assert result == 1
        assert "failed" in captured.out
