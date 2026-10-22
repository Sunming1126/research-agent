from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve", "case", "batch", "precheck"])
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    if args.command != "serve":
        os.environ["RESEARCH_RUNTIME"] = "local"
    if args.env_file:
        for line in args.env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    if args.config:
        os.environ["RESEARCH_CONFIG"] = str(args.config)
    from .core.engine import ResearchAgentEngine
    from .core.utils import read_json
    engine = ResearchAgentEngine()
    if args.command == "serve":
        import agent
        if agent.AgentRuntime:
            agent.AgentRuntime(agent.ResearchAgent(engine)).run()
        else:
            agent.serve_local(engine).serve_forever()
        return
    if args.command == "case":
        if not args.input:
            parser.error("--input required")
        result = engine.solve_sync(read_json(args.input))
    elif args.command == "batch":
        result = engine.solve_sync({"dispatch": True})
    else:
        result = engine.precheck()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "batch" and not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

