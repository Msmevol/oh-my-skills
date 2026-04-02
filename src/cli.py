"""
CLI Entry Point for Skill Runner

Usage:
  skill-runner <skill-name> [user-request]
  skill-runner --list
  skill-runner --help
"""

import sys
import os
import argparse
import time
import json
import logging

# Fix Windows console encoding
if sys.platform == "win32":
    import ctypes

    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.skill_loader import SkillLoader, SkillNotFoundError
from src.opencode_client import OpenCodeClient

logger = logging.getLogger(__name__)


def create_parser():
    parser = argparse.ArgumentParser(
        description="Skill Runner - Execute opencode skills with automatic recovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  skill-runner code-mr-ci-loop "Create MR"
  skill-runner code-mr-ci-loop "Create MR" --format json
  skill-runner --list
  skill-runner code-mr-ci-loop "Create MR" --url http://localhost:4096
        """,
    )

    parser.add_argument(
        "skill_name",
        nargs="?",
        help="Skill name to execute (kebab-case)",
    )
    parser.add_argument(
        "user_request",
        nargs="?",
        default=None,
        help="User request for the skill",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available skills",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:4096",
        help="OpenCode server URL (default: http://localhost:4096)",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Working directory to search for skills",
    )
    parser.add_argument(
        "--agent",
        default="skill-executor",
        help="Agent name to use (default: skill-executor)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Max execution time in seconds (default: 600)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )

    return parser


def setup_logging(log_file=None, quiet=False):
    handlers = []
    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    if not quiet:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_list(loader, format_type="text"):
    skills = loader.list_skills()

    if format_type == "json":
        print(json.dumps(skills, indent=2))
        return 0

    if not skills:
        print("No skills found.")
        print("\nPlace skills in:")
        print("  .opencode/skills/<name>/SKILL.md")
        print("  .claude/skills/<name>/SKILL.md")
        print("  .agents/skills/<name>/SKILL.md")
        return 0

    print("Available skills:")
    for skill in skills:
        print(f"  {skill['name']:<20} {skill['description']}")
    print(f"\nTotal: {len(skills)} skills")
    return 0


def cmd_run(skill_name, user_request, args):
    loader = SkillLoader(search_dirs=[args.dir] if args.dir else None)

    print(f"Skill: {skill_name}")
    if user_request:
        print(f"Request: {user_request}")
    print("=" * 60)

    if args.format == "text":
        print(f"Connecting to opencode server at {args.url}...")

    client = OpenCodeClient(args.url)
    if not client.health_check():
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error": f"Cannot connect to opencode server at {args.url}",
                    },
                    indent=2,
                )
            )
            return 1
        print(f"✗ Cannot connect to opencode server at {args.url}")
        print("Make sure opencode serve is running.")
        return 1

    if args.format == "text":
        print("✓ Connected")

    try:
        if args.format == "text":
            print(f"Loading skill: {skill_name}...")
        skill_data = loader.load_skill(skill_name)
        if args.format == "text":
            print(f"✓ Skill loaded from: {skill_data['path']}")
            print(f"  Description: {skill_data['description']}")
    except SkillNotFoundError as e:
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error": f"Skill not found: {skill_name}",
                    },
                    indent=2,
                )
            )
            return 1
        print(f"✗ {e}")
        return 1

    if args.format == "text":
        print("Executing skill via opencode run...")

    start_time = time.time()

    result = client.run_skill(
        skill_name=skill_name,
        user_request=user_request or skill_data["description"],
        agent=args.agent,
        timeout=args.timeout,
        working_dir=args.dir,
    )

    elapsed = time.time() - start_time

    if args.format == "json":
        output = {
            "status": result["status"],
            "skill_name": skill_name,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
        if result.get("error"):
            output["error"] = result["error"]
        print(json.dumps(output, indent=2))
    else:
        print()
        if result.get("stdout"):
            print(result["stdout"])
        if result.get("stderr"):
            print(result["stderr"], file=sys.stderr)

        print("=" * 60)
        print(f"Status: {result['status']}")
        print(f"Skill: {skill_name}")
        print(f"Elapsed: {elapsed:.1f}s")
        if result.get("error"):
            print(f"Error: {result['error']}")
        print("=" * 60)

    return 0 if result["status"] == "success" else 1


def main():
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(log_file=args.log_file, quiet=args.quiet)

    loader = SkillLoader(search_dirs=[args.dir] if args.dir else None)

    if args.list:
        return cmd_list(loader, args.format)

    if not args.skill_name:
        parser.print_help()
        return 1

    return cmd_run(args.skill_name, args.user_request, args)


if __name__ == "__main__":
    sys.exit(main())
