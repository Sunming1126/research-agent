from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .utils import confined, read_json, safe_relative_path


DEFAULT_WORKSPACE_TEAM = "workspace_team"  # 仅本地兜底；正式平台注入目录标识。


@dataclass
class Settings:
    """运行配置；材料根目录必须是独立授权目录。"""
    workspace: Path
    output: Path
    team: str = DEFAULT_WORKSPACE_TEAM
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
    verify_assigned: bool = False

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
        output = Path(os.environ.get("RESEARCH_RESULT_ROOT", config.get("output", "output")))
        roots = os.environ.get("RESEARCH_MATERIAL_ROOTS")
        if roots is not None:
            material_roots = [Path(x) for x in roots.split(os.pathsep) if x]
        elif os.environ.get("PAPERS_PATH"):
            material_roots = [Path(os.environ["PAPERS_PATH"])]
        elif "material_roots" in config:
            material_roots = [Path(x) for x in config["material_roots"]]
        else:
            # 官方挂载 /data/papers；旧中文库仅作本地兼容，不扫描队伍目录。
            library = "论文库" if workspace.resolve().name == "data" else "data/论文库"
            canonical = confined(workspace, library)
            papers = confined(workspace, "papers" if workspace.resolve().name == "data" else "data/papers")
            candidates = [papers, canonical, confined(output, "data/论文库")]
            existing = list(dict.fromkeys(path for path in candidates if path.is_dir()))
            if papers in existing:
                material_roots = [papers]
            elif len(existing) > 1:
                raise ValueError("multiple paper libraries found; configure RESEARCH_MATERIAL_ROOTS explicitly")
            else:
                material_roots = existing or [papers]
        aliases = {}
        if len(material_roots) == 1 and material_roots[0].name in {"论文库", "papers"}:
            aliases["本地论文文件夹内的 PDF"] = [material_roots[0].name]
        aliases.update(config.get("material_aliases", {}))
        # 整体库的等价写法必须一致；含主题/“指定”等限定的材料不会被扩成整库。
        library_scope = aliases.get("本地论文文件夹内的 PDF")
        if library_scope is not None:
            for description in ("本地论文库中的 PDF", "本地论文库内的 PDF", "本地论文文件夹中的 PDF",
                                "本地论文文件夹内的所有 PDF", "本地论文库中的所有 PDF", "本地论文库中的全部 PDF",
                                "本地论文库", "本地论文文件夹"):
                aliases.setdefault(description, library_scope)
        return cls(
            workspace=workspace,
            output=output,
            # TEAM_ID 用于遥测身份，不得拿来拼结果目录；也不使用真实报名队名。
            team=(os.environ.get("RESEARCH_WORKSPACE_TEAM") or os.environ.get("TEAM_NAME")
                  or config.get("team") or DEFAULT_WORKSPACE_TEAM),
            material_roots=material_roots,
            material_aliases=aliases,
            experiment_mode=os.environ.get("RESEARCH_EXPERIMENT_MODE", config.get("experiment_mode", "platform")),
            local_experiment_root=Path(config["local_experiment_root"]) if config.get("local_experiment_root") else None,
            local_python=config.get("local_python", ""),
            experiment_protocol=os.environ.get("RESEARCH_EXPERIMENT_PROTOCOL", config.get("experiment_protocol", "auto")),
            ocr=config.get("ocr", False),
            llm_calls=config.get("llm_calls", 30), llm_tokens=config.get("llm_tokens", 40000),
            case_timeout=config.get("case_timeout", 180),
            verify_assigned=bool(os.environ.get("RESEARCH_WORKSPACE_TEAM")),
        )

