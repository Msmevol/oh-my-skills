"""
Main entry point for the skill orchestrator
Usage:
  python main.py <skill-name-or-path> [user-request]
  python main.py --list
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LOG_FORMAT, LOG_LEVEL, SERVER_PORT
from src.orchestrator import Orchestrator


def main():
    logging.basicConfig(level=getattr(logging, LOG_LEVEL), format=LOG_FORMAT)

    # --list: 列出所有可用 skills
    if "--list" in sys.argv:
        orchestrator = Orchestrator(port=SERVER_PORT)
        skills = orchestrator.list_skills()
        if skills:
            print("Available skills:")
            for s in skills:
                print(f"  {s['name']}: {s['description']}")
                print(f"    Path: {s['path']}")
        else:
            print("No skills found.")
            print("\nPlace skills in:")
            print("  .opencode/skills/<name>/SKILL.md")
            print("  .agents/skills/<name>/SKILL.md")
        return 0

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python main.py <skill-name-or-path> [user-request]")
        print("  python main.py --list")
        print("\nExamples:")
        print('  python main.py code-mr-ci-loop "创建MR"')
        print("  python main.py D:/ai_demo/skill.txt")
        print("  python main.py --list")
        sys.exit(1)

    skill_input = sys.argv[1]
    user_request = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None

    print(f"Skill: {skill_input}")
    if user_request:
        print(f"Request: {user_request}")
    print("=" * 60)

    orchestrator = Orchestrator(port=SERVER_PORT)
    result = orchestrator.run(skill_input, user_request)

    print("=" * 60)
    print(f"Status: {result['status']}")

    if result["status"] == "success":
        print("Skill executed successfully!")
        if result.get("skill_name"):
            print(f"\nSkill: {result['skill_name']}")
        if result.get("progress"):
            p = result["progress"]
            print(
                f"\nProgress: {p.get('completed', 0)}/{p.get('total', 0)} "
                f"({p.get('percentage', 0)}%)"
            )
    else:
        print(f"Error: {result.get('error', 'Unknown error')}")

    print("=" * 60)

    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
