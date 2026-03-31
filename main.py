"""
Main entry point for the skill orchestrator
Usage: python main.py path/to/skill.txt
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import LOG_FORMAT, LOG_LEVEL, SERVER_PORT
from src.orchestrator import Orchestrator


def main():
    logging.basicConfig(level=getattr(logging, LOG_LEVEL), format=LOG_FORMAT)

    if len(sys.argv) < 2:
        print("Usage: python main.py <skill-file>")
        print('Example: python main.py "path/to/skill.txt"')
        sys.exit(1)

    skill_path = " ".join(sys.argv[1:])

    print(f"Skill: {skill_path}")
    print("=" * 60)

    orchestrator = Orchestrator(port=SERVER_PORT)
    result = orchestrator.run(skill_path)

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
