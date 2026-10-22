from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .utils import read_json, safe_relative_path


@dataclass
class Settings:
    """运行配置；材料根目录必须是独立授权目录。"""
    workspace: Path
    output: Path
    team: str = "workspace_team"
    material_roots: list[Path] = field(default_factory=list)
    material_aliases: dict[str, list[str]] = field(default_factory=dict)
    experiment_mode: str = "platform"
    local_experiment_root: Path | None = None
    local_python: str = ""
    experiment_protocol: str = "auto"
    ocr: bool = False
    llm_calls: int = 30
    llm_tokens: int = 40000
    case_timeout: int = 180

    def __post_init__(self):
        if not safe_relative_path(self.team) or "/" in self.team or self.team.endswith((".", " ")):
            raise ValueError("team must be one safe directory name")
        self.workspace = self.workspace.resolve()
        self.output = self.output.resolve()
        self.material_roots = [root.resolve() for root in self.material_roots]
        if any(root in {self.workspace, self.workspace / "data", self.output} for root in self.material_roots):
            raise ValueError("configure dedicated material directories, not workspace/data/output")
        if self.experiment_mode not in {"platform", "local"}:
            raise ValueError("invalid experiment mode")
        if self.experiment_protocol not in {"auto", "legacy", "layered-d-v3"}:
            raise ValueError("invalid experiment protocol")
        if min(self.llm_calls, self.llm_tokens, self.case_timeout) <= 0:
            raise ValueError("case budgets must be positive")

    @classmethod
    def from_env(cls):
        config = read_json(Path(os.environ["RESEARCH_CONFIG"])) if os.environ.get("RESEARCH_CONFIG") else {}
        workspace = Path(os.environ.get("RESEARCH_WORKSPACE_ROOT", config.get("workspace", "/data" if os.name != "nt" else ".")))
        roots = os.environ.get("RESEARCH_MATERIAL_ROOTS")
        return cls(
            workspace=workspace,
            output=Path(os.environ.get("RESEARCH_RESULT_ROOT", config.get("output", "output"))),
            team=os.environ.get("RESEARCH_WORKSPACE_TEAM", config.get("team", "workspace_team")),
            material_roots=[Path(x) for x in (roots.split(os.pathsep) if roots else config.get("material_roots", []))],
            material_aliases=config.get("material_aliases", {}),
            experiment_mode=os.environ.get("RESEARCH_EXPERIMENT_MODE", config.get("experiment_mode", "platform")),
            local_experiment_root=Path(config["local_experiment_root"]) if config.get("local_experiment_root") else None,
            local_python=config.get("local_python", ""),
            experiment_protocol=os.environ.get("RESEARCH_EXPERIMENT_PROTOCOL", config.get("experiment_protocol", "auto")),
            ocr=config.get("ocr", False),
            llm_calls=config.get("llm_calls", 30), llm_tokens=config.get("llm_tokens", 40000),
            case_timeout=config.get("case_timeout", 180),
        )

