"""
Skill Loader - 查找和解析标准 opencode skill 文件

支持标准 opencode skill 格式：
- .opencode/skills/<name>/SKILL.md
- .claude/skills/<name>/SKILL.md
- .agents/skills/<name>/SKILL.md
"""

import os
import re
from typing import Optional, Dict, Any, List
from pathlib import Path

SKILL_LOCATIONS = [
    ".opencode/skills/{name}/SKILL.md",
    ".claude/skills/{name}/SKILL.md",
    ".agents/skills/{name}/SKILL.md",
]

GLOBAL_SKILL_LOCATIONS = [
    os.path.expanduser("~/.config/opencode/skills/{name}/SKILL.md"),
    os.path.expanduser("~/.claude/skills/{name}/SKILL.md"),
    os.path.expanduser("~/.agents/skills/{name}/SKILL.md"),
]

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SkillNotFoundError(Exception):
    pass


class SkillLoader:
    def __init__(self, search_dirs: Optional[List[str]] = None):
        self.search_dirs = search_dirs or [os.getcwd()]

    def find_skill(self, name: str) -> str:
        if not SKILL_NAME_PATTERN.match(name):
            raise SkillNotFoundError(
                f"Invalid skill name: {name}. "
                f"Must match pattern: ^[a-z0-9]+(-[a-z0-9]+)*$"
            )

        all_locations = []
        for base_dir in self.search_dirs:
            for location in SKILL_LOCATIONS:
                path = os.path.join(base_dir, location.format(name=name))
                all_locations.append(path)

        for location in GLOBAL_SKILL_LOCATIONS:
            all_locations.append(location.format(name=name))

        for path in all_locations:
            if os.path.isfile(path):
                return os.path.abspath(path)

        raise SkillNotFoundError(
            f"Skill not found: {name}\n\n"
            f"Searched locations:\n" + "\n".join(f"  - {p}" for p in all_locations)
        )

    def load_skill(self, name: str) -> Dict[str, Any]:
        path = self.find_skill(name)

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        metadata = self._parse_frontmatter(content)

        return {
            "name": metadata.get("name", name),
            "description": metadata.get("description", ""),
            "content": content,
            "path": path,
            "metadata": {
                k: v for k, v in metadata.items() if k not in ("name", "description")
            },
        }

    def _parse_frontmatter(self, content: str) -> Dict[str, Any]:
        if not content.startswith("---"):
            return {}

        end_idx = content.find("---", 3)
        if end_idx == -1:
            return {}

        yaml_block = content[3:end_idx].strip()

        result = {}
        for line in yaml_block.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, value = line.split(":", 1)
                value = value.strip().strip('"').strip("'")
                result[key.strip()] = value

        return result

    def list_skills(self) -> List[Dict[str, str]]:
        skills = []
        seen_names = set()

        all_dirs = list(self.search_dirs)
        for loc in GLOBAL_SKILL_LOCATIONS:
            base = loc.split("/skills/")[0] + "/skills"
            if os.path.isdir(base):
                all_dirs.append(base)

        for base_dir in all_dirs:
            for location_template in SKILL_LOCATIONS:
                base_path = os.path.join(
                    base_dir, location_template.split("/skills/")[0], "skills"
                )
                if not os.path.isdir(base_path):
                    continue

                for skill_dir in os.listdir(base_path):
                    skill_file = os.path.join(base_path, skill_dir, "SKILL.md")
                    if os.path.isfile(skill_file) and skill_dir not in seen_names:
                        try:
                            with open(skill_file, "r", encoding="utf-8") as f:
                                content = f.read()
                            metadata = self._parse_frontmatter(content)
                            name = metadata.get("name", skill_dir)
                            description = metadata.get("description", "")
                            if name and name not in seen_names:
                                seen_names.add(name)
                                skills.append(
                                    {
                                        "name": name,
                                        "description": description,
                                        "path": os.path.abspath(skill_file),
                                    }
                                )
                        except Exception:
                            continue

        return sorted(skills, key=lambda x: x["name"])
