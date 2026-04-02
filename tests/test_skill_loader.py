"""
Tests for Skill Loader
"""

import pytest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.skill_loader import SkillLoader, SkillNotFoundError


@pytest.fixture
def skill_dir():
    temp_dir = tempfile.mkdtemp()
    skill_path = os.path.join(temp_dir, ".opencode", "skills", "test-skill")
    os.makedirs(skill_path)

    skill_content = """---
name: test-skill
description: A test skill for testing
license: MIT
---

# Test Skill

This is a test skill.

## Steps
1. Do something
2. Verify result
"""
    with open(os.path.join(skill_path, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_content)

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


class TestSkillNameValidation:
    def test_valid_names(self):
        loader = SkillLoader()
        valid_names = ["test", "my-skill", "code-mr-ci-loop", "skill123", "a-b-c"]
        for name in valid_names:
            assert (
                loader._parse_frontmatter("---\nname: " + name + "\n---")["name"]
                == name
            )

    def test_invalid_names(self):
        loader = SkillLoader()
        with pytest.raises(SkillNotFoundError):
            loader.find_skill("Invalid Name")
        with pytest.raises(SkillNotFoundError):
            loader.find_skill("invalid_name")
        with pytest.raises(SkillNotFoundError):
            loader.find_skill("--invalid")


class TestSkillFinding:
    def test_find_skill_in_opencode_dir(self, skill_dir):
        loader = SkillLoader(search_dirs=[skill_dir])
        path = loader.find_skill("test-skill")
        assert path.endswith("test-skill/SKILL.md") or path.endswith(
            "test-skill\\SKILL.md"
        )
        assert os.path.isfile(path)

    def test_find_skill_not_found(self, skill_dir):
        loader = SkillLoader(search_dirs=[skill_dir])
        with pytest.raises(SkillNotFoundError) as exc_info:
            loader.find_skill("nonexistent")
        assert "Skill not found" in str(exc_info.value)
        assert "nonexistent" in str(exc_info.value)

    def test_find_skill_invalid_name(self):
        loader = SkillLoader()
        with pytest.raises(SkillNotFoundError):
            loader.find_skill("Invalid Name")


class TestSkillLoading:
    def test_load_skill(self, skill_dir):
        loader = SkillLoader(search_dirs=[skill_dir])
        skill = loader.load_skill("test-skill")

        assert skill["name"] == "test-skill"
        assert skill["description"] == "A test skill for testing"
        assert "Test Skill" in skill["content"]
        assert skill["path"].endswith("test-skill/SKILL.md") or skill["path"].endswith(
            "test-skill\\SKILL.md"
        )
        assert skill["metadata"].get("license") == "MIT"

    def test_load_skill_minimal(self, skill_dir):
        minimal_dir = os.path.join(skill_dir, ".opencode", "skills", "minimal")
        os.makedirs(minimal_dir)

        with open(os.path.join(minimal_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: minimal\ndescription: Minimal skill\n---\n\n# Minimal\n"
            )

        loader = SkillLoader(search_dirs=[skill_dir])
        skill = loader.load_skill("minimal")

        assert skill["name"] == "minimal"
        assert skill["description"] == "Minimal skill"
        assert skill["metadata"] == {}


class TestSkillListing:
    def test_list_skills(self, skill_dir):
        loader = SkillLoader(search_dirs=[skill_dir])
        skills = loader.list_skills()

        assert len(skills) >= 1
        names = [s["name"] for s in skills]
        assert "test-skill" in names

    def test_list_skills_empty_dir(self):
        temp_dir = tempfile.mkdtemp()
        try:
            loader = SkillLoader(search_dirs=[temp_dir])
            skills = loader.list_skills()
            assert skills == []
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestFrontmatterParsing:
    def test_parse_simple_frontmatter(self):
        loader = SkillLoader()
        content = """---
name: my-skill
description: My skill description
---

# Content
"""
        result = loader._parse_frontmatter(content)
        assert result["name"] == "my-skill"
        assert result["description"] == "My skill description"

    def test_parse_no_frontmatter(self):
        loader = SkillLoader()
        content = "# No frontmatter\n"
        result = loader._parse_frontmatter(content)
        assert result == {}

    def test_parse_incomplete_frontmatter(self):
        loader = SkillLoader()
        content = "---\nname: test\n"
        result = loader._parse_frontmatter(content)
        assert result == {}

    def test_parse_quoted_values(self):
        loader = SkillLoader()
        content = """---
name: my-skill
description: "Description with: colon"
---
"""
        result = loader._parse_frontmatter(content)
        assert result["description"] == "Description with: colon"
