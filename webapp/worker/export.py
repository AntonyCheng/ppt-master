"""Re-export a manually edited SVG project into a fresh PPTX artifact."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


WORKSPACE_ROOT = Path("/workspace/project")
SKILL_ROOT = Path("/app/skills/ppt-master")


def emit(event_type: str, **payload: object) -> None:
    """Write one runner-readable event without leaking command internals."""

    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def _authoring_root(workspace: Path) -> Path:
    """Find the one SVG authoring root inside the mounted job workspace."""

    if (workspace / "svg_output").is_dir():
        return workspace
    candidates = [item for item in workspace.iterdir() if (item / "svg_output").is_dir()]
    if len(candidates) != 1:
        raise RuntimeError("工作区中没有唯一的 SVG 作者目录")
    return candidates[0]


def _run(command: list[str], message: str) -> None:
    """Run one required export gate and surface its terminal output on failure."""

    emit("status", status="exporting", message=message)
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return
    detail = (completed.stderr or completed.stdout or "导出命令失败").strip()
    raise RuntimeError(detail[-1800:])


def main() -> int:
    """Validate the edited SVG roster and create a fresh native PPTX."""

    try:
        project_root = _authoring_root(WORKSPACE_ROOT)
        _run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "svg_quality_checker.py"),
                str(project_root),
                "--quick-generate",
                "--stage",
                "final",
                "--json",
            ],
            "正在校验编辑后的演示文稿",
        )
        _run(
            [
                sys.executable,
                str(SKILL_ROOT / "scripts" / "svg_to_pptx.py"),
                str(project_root),
                "--quick-generate",
                "--no-notes",
            ],
            "正在导出新的演示文稿",
        )
        emit("status", status="exported", message="新版演示文稿已导出")
        return 0
    except Exception as exc:  # noqa: BLE001
        emit("error", message=f"手动编辑导出失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
