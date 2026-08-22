"""Execute one PPT Master quick-generation job inside a restricted container."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
import sys
from threading import Thread
from time import monotonic
import zipfile
from xml.etree import ElementTree

WORKSPACE = Path("/workspace/project")
REPO_ROOT = Path("/app")
AUTHORING_ROOT_NAME = "ppt-project"
OPENCODE_CONFIG_SOURCE = Path("/opt/pptmaster/opencode-config")
OPENCODE_CONFIG_DESTINATION = Path("/home/pptmaster/.config/opencode")
OPENCODE_IDLE_TIMEOUT_EXIT_CODE = 124
DEFAULT_OPENCODE_IDLE_TIMEOUT_SECONDS = 600
OPENCODE_WAIT_NOTICE_SECONDS = 60


def emit(event_type: str, **payload: object) -> None:
    """Write one JSON line consumed by the trusted runner."""

    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def run_command(command: list[str], *, idle_timeout_seconds: int | None = None) -> int:
    """Run a command, stream events, and stop it if OpenCode becomes silent."""

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    if idle_timeout_seconds is None:
        for line in process.stdout:
            _emit_command_line(line.strip())
        return process.wait()

    output_queue: Queue[str | None] = Queue()

    def copy_output() -> None:
        """Forward blocking subprocess output into the timeout-aware loop."""

        for line in process.stdout:
            output_queue.put(line)
        output_queue.put(None)

    Thread(target=copy_output, daemon=True).start()
    last_output_at = monotonic()
    last_wait_notice_at = 0
    while True:
        try:
            line = output_queue.get(timeout=1)
        except Empty:
            idle_seconds = int(monotonic() - last_output_at)
            if idle_seconds < idle_timeout_seconds:
                if idle_seconds - last_wait_notice_at >= OPENCODE_WAIT_NOTICE_SECONDS:
                    emit(
                        "log",
                        text=(
                            f"OpenCode 暂无新输出，已等待 {idle_seconds} 秒；"
                            "仍在等待模型响应。"
                        ),
                    )
                    last_wait_notice_at = idle_seconds
                continue
            emit(
                "error",
                message=(
                    f"OpenCode 连续 {idle_timeout_seconds} 秒未输出，已停止该任务；"
                    "请重新提交，或检查所选模型服务是否正常响应。"
                ),
            )
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return OPENCODE_IDLE_TIMEOUT_EXIT_CODE
        if line is None:
            break
        last_output_at = monotonic()
        last_wait_notice_at = 0
        _emit_command_line(line.strip())
    return process.wait()


def _emit_command_line(line: str) -> None:
    """Translate OpenCode JSONL into small, user-visible execution events."""

    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        emit("agent", text=line[:1000])
        return
    event_type = event.get("type")
    part = event.get("part") or {}
    if event_type == "text":
        text = _strip_ansi(str(part.get("text") or "").strip())
        if text:
            if "permission requested:" in text.lower():
                emit("permission", message=text[:1000])
                return
            emit("agent", text=text[:1000])
        return
    if event_type == "tool_use":
        state = part.get("state") or {}
        if state.get("status") != "completed":
            return
        tool = str(part.get("tool") or "tool")
        input_data = state.get("input") or {}
        detail = str(
            input_data.get("filePath")
            or input_data.get("file_path")
            or input_data.get("command")
            or ""
        )
        emit("tool", tool=tool, detail=detail[:240])
        return
    if event_type == "step_finish":
        tokens = part.get("tokens") or {}
        emit(
            "usage",
            tokens=tokens,
            total_tokens=tokens.get("total"),
            cost=part.get("cost"),
        )
        return
    emit("opencode", event=event_type or "unknown")


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip_ansi(value: str) -> str:
    """Remove terminal control sequences before persisting execution events."""

    return _ANSI_RE.sub("", value)


def install_opencode_config() -> bool:
    """Copy the read-only provider config into this worker's writable Home directory."""

    generated = os.environ.get("PPTMASTER_OPENCODE_CONFIG_JSON", "")
    if generated:
        try:
            json.loads(generated)
            OPENCODE_CONFIG_DESTINATION.mkdir(parents=True, exist_ok=True)
            (OPENCODE_CONFIG_DESTINATION / "opencode.json").write_text(generated, encoding="utf-8")
            return True
        except (OSError, json.JSONDecodeError) as exc:
            emit("error", message=f"OpenCode configuration setup failed: {exc}")
            return False
    if not OPENCODE_CONFIG_SOURCE.exists():
        return True
    source = next(
        (
            candidate
            for candidate in (
                OPENCODE_CONFIG_SOURCE / "opencode.jsonc",
                OPENCODE_CONFIG_SOURCE / "opencode.json",
            )
            if candidate.is_file()
        ),
        None,
    )
    if source is None:
        emit(
            "error",
            message="OpenCode configuration source does not contain opencode.jsonc or opencode.json",
        )
        return False
    try:
        OPENCODE_CONFIG_DESTINATION.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, OPENCODE_CONFIG_DESTINATION / source.name)
    except OSError as exc:
        emit("error", message=f"OpenCode configuration setup failed: {exc}")
        return False
    return True


def job_project_workspace(job_id: str, continue_mode: bool = False) -> Path:
    """Return the stable authoring root for this Job."""

    del job_id
    if continue_mode:
        return WORKSPACE / AUTHORING_ROOT_NAME
    return WORKSPACE / f"{AUTHORING_ROOT_NAME}_ppt169_{datetime.now():%Y%m%d}"


def opencode_idle_timeout_seconds() -> int:
    """Read the bounded no-output timeout for one OpenCode invocation."""

    raw_value = os.environ.get("PPTMASTER_OPENCODE_IDLE_TIMEOUT_SECONDS", "")
    try:
        configured_value = int(raw_value) if raw_value else DEFAULT_OPENCODE_IDLE_TIMEOUT_SECONDS
    except ValueError:
        configured_value = DEFAULT_OPENCODE_IDLE_TIMEOUT_SECONDS
    return min(max(configured_value, 60), 1800)


def verify_project_workspace(project_workspace: Path) -> bool:
    """Reject jobs that moved or duplicated the Worker-initialized project root."""

    if not project_workspace.is_dir() or not (project_workspace / "svg_output").is_dir():
        emit(
            "error",
            message=(
                "OpenCode 改变了受管项目目录，预期工作区不可用："
                f"{project_workspace}"
            ),
        )
        return False
    unexpected_roots = [
        path
        for path in WORKSPACE.iterdir()
        if path.is_dir()
        and path.resolve() != project_workspace.resolve()
        and (path / "svg_output").is_dir()
    ]
    if unexpected_roots:
        emit(
            "error",
            message=(
                "OpenCode 创建了未授权的第二项目目录："
                f"{', '.join(str(path) for path in unexpected_roots)}；"
                "任务产物必须保留在系统指定工作区。"
            ),
        )
        return False
    return True


def _snapshot(project_workspace: Path) -> dict[str, str]:
    """Hash authored SVG and exported PPTX files for deterministic comparison."""

    snapshot: dict[str, str] = {}
    for folder, pattern in (("svg_output", "*.svg"), ("exports", "*.pptx")):
        root = project_workspace / folder
        if not root.is_dir():
            continue
        for path in sorted(root.glob(pattern)):
            # Keep manifest keys portable so local Windows checks match the Linux worker.
            snapshot[path.relative_to(project_workspace).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _slide_paths(project_workspace: Path, page_number: int | None) -> list[Path]:
    paths = sorted((project_workspace / "svg_output").glob("*.svg"))
    if page_number is None:
        return paths
    prefix = f"{page_number:02d}_"
    return [path for path in paths if path.name.startswith(prefix)]


def _pptx_slide_texts(project_workspace: Path, page_number: int | None) -> str:
    """Read editable DrawingML text from the latest exported PPTX."""

    exports = sorted((project_workspace / "exports").glob("*.pptx"), key=lambda path: path.stat().st_mtime)
    if not exports:
        return ""
    with zipfile.ZipFile(exports[-1]) as archive:
        if page_number is not None:
            names = [f"ppt/slides/slide{page_number}.xml"]
        else:
            names = [name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
        texts: list[str] = []
        for name in names:
            if name not in archive.namelist():
                continue
            root = ElementTree.fromstring(archive.read(name))
            texts.extend(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    return "\n".join(texts)


def _replacement_requests(prompt: str) -> list[dict[str, object]]:
    """Extract explicit quoted A-to-B edits that can be checked exactly."""

    pattern = re.compile(
        r"第\s*(?P<page>\d+|一|二|三|四|五|六|七|八|九|十)\s*页.*?"
        r"从[\"“「](?P<old>.+?)[\"”」]\s*(?:修改为|修改|改为|改成|替换为|更改为)\s*"
        r"[\"“「](?P<new>.+?)[\"”」]"
    )
    page_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    requests: list[dict[str, object]] = []
    for match in pattern.finditer(prompt):
        raw_page = match.group("page")
        page = page_map.get(raw_page, int(raw_page) if raw_page.isdigit() else None)
        requests.append({"page": page, "old": match.group("old"), "new": match.group("new")})
    return requests


def _target_slide_number() -> int | None:
    raw = os.environ.get("PPTMASTER_TARGET_SLIDE", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _validate_revision(
    project_workspace: Path,
    baseline: dict[str, str],
    prompt: str,
    continue_mode: bool,
    target_slide: int | None,
) -> dict[str, object]:
    """Verify that the generated or continued authoring output is deliverable."""

    current = _snapshot(project_workspace)
    changed = sorted(
        path for path in set(baseline) | set(current) if baseline.get(path) != current.get(path)
    )
    svg_paths = _slide_paths(project_workspace, None)
    baseline_svg = {path for path in baseline if path.startswith("svg_output/")}
    current_svg = {f"svg_output/{path.name}" for path in svg_paths}
    target_svg = {
        f"svg_output/{path.name}"
        for path in _slide_paths(project_workspace, target_slide)
    }
    changed_svg_files = sorted(path for path in changed if path.startswith("svg_output/"))
    unexpected_changed_slides = sorted(
        path for path in changed_svg_files if target_slide is not None and path not in target_svg
    )
    target_changed = sorted(path for path in changed_svg_files if path in target_svg)
    replacements = _replacement_requests(prompt)
    checks: list[dict[str, object]] = []
    for request in replacements:
        paths = _slide_paths(project_workspace, request["page"])
        content = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.is_file())
        pptx_content = _pptx_slide_texts(project_workspace, request["page"])
        old, new = str(request["old"]), str(request["new"])
        checks.append({
            "page": request["page"],
            "old": old,
            "new": new,
            "passed": bool(paths)
            and new in content
            and old not in content
            and new in pptx_content
            and old not in pptx_content,
        })
    changed_svg = any(path.startswith("svg_output/") for path in changed)
    changed_pptx = any(path.startswith("exports/") for path in changed)
    passed = bool(svg_paths) and changed_pptx
    if continue_mode:
        passed = passed and changed_svg
        if target_slide is not None:
            passed = passed and bool(target_svg) and bool(target_changed)
            passed = passed and not unexpected_changed_slides and baseline_svg == current_svg
    if checks:
        passed = passed and all(bool(check["passed"]) for check in checks)
    validation_type = "修改" if continue_mode else "生成"
    result = {
        "passed": passed,
        "continuation": continue_mode,
        "changed_files": changed,
        "changed_svg": changed_svg,
        "changed_pptx": changed_pptx,
        "target_slide_number": target_slide,
        "changed_target_slides": target_changed,
        "unexpected_changed_slides": unexpected_changed_slides,
        "slide_roster_unchanged": baseline_svg == current_svg if continue_mode else True,
        "checks": checks,
        "message": (
            f"{validation_type}校验通过"
            if passed
            else f"{validation_type}未生效：未检测到符合要求的 SVG/PPTX 产物"
        ),
    }
    (project_workspace / "validation").mkdir(parents=True, exist_ok=True)
    (project_workspace / "validation" / "change_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    """Initialize the mounted workspace and run OpenCode in quick-generate mode."""

    job_id = os.environ.get("PPTMASTER_JOB_ID", "")
    prompt = os.environ.get("PPTMASTER_JOB_PROMPT", "").strip()
    model = os.environ.get("PPTMASTER_JOB_MODEL", "").strip()
    if not job_id or not prompt:
        emit("error", message="PPTMASTER_JOB_ID and PPTMASTER_JOB_PROMPT are required")
        return 2
    continue_mode = os.environ.get("PPTMASTER_CONTINUE") == "1"
    target_slide = _target_slide_number()
    template_root = os.environ.get("PPTMASTER_TEMPLATE_ROOT", "").strip()
    project_workspace = job_project_workspace(job_id, continue_mode)
    baseline: dict[str, str] = {}
    emit("status", status="initializing")
    if not install_opencode_config():
        return 1
    if continue_mode:
        if not project_workspace.is_dir() or not any(project_workspace.iterdir()):
            emit("error", message="Base PPT Master workspace is missing or empty")
            return 1
        emit("status", status="continuing")
        baseline = _snapshot(project_workspace)
        if target_slide is not None and not _slide_paths(project_workspace, target_slide):
            emit("error", message=f"目标页面不存在：第 {target_slide} 页")
            return 1
        workspace_instruction = (
            "The workspace contains the last successful revision. Modify that existing PPT Master "
            "project in place and preserve all unaffected slides."
        )
    else:
        init_command = [
            sys.executable,
            "skills/ppt-master/scripts/project_manager.py",
            "init",
            project_workspace.name,
            "--dir",
            str(WORKSPACE),
            "--format",
            "ppt169",
            "--quick-generate",
        ]
        if run_command(init_command) != 0:
            emit("error", message="Project workspace initialization failed")
            return 1
        workspace_instruction = "The Worker has already initialized the empty project workspace."
    template_instruction = ""
    if template_root:
        selected_template = Path(template_root)
        if not (selected_template / "templates" / "design_spec.md").is_file():
            emit("error", message="Selected template workspace is unavailable")
            return 1
        emit("template", message="正在应用所选模板")
        template_instruction = (
            f"Use the selected PPT Master template workspace at {selected_template} as the exact "
            "template workspace for this run. Read and apply it while generating. Do not modify, "
            "move, or duplicate that template workspace."
        )
    agent_prompt = f"""You are executing one autonomous PPT Master generation job.

Read and follow /app/AGENTS.md and its referenced ppt-master Skill. The web request is
explicit quick-generation intent, so use the Skill's Quick Generate runtime without an
interactive confirmation gate. The only project workspace is {project_workspace}; {workspace_instruction}
It is the immutable project root for this job. Do not run project_manager.py init, and do
not move, rename, copy, or create another project directory. Author every project file
directly beneath this exact path.
{template_instruction}
For continuation edits, modify the existing SVG authoring files directly and preserve all
unaffected slides. If a target slide is provided, only that slide's SVG may change; do not
modify any other slide SVG, even if a broader redesign seems helpful. Do not run sudo, inspect /proc, inspect host permissions, or probe the
container environment; those checks are unrelated to the presentation edit. Create a
native editable PPTX, run the required quality checks, and export the final .pptx into
{project_workspace}/exports. Do not access files outside /workspace/project except the installed
PPT Master Skill and its declared tools.

User request:
{prompt}
"""
    command = ["opencode", "run", "--format", "json"]
    if model:
        command.extend(["--model", model])
    command.append(agent_prompt)
    emit("status", status="running")
    return_code = run_command(command, idle_timeout_seconds=opencode_idle_timeout_seconds())
    if return_code:
        if return_code == OPENCODE_IDLE_TIMEOUT_EXIT_CODE:
            return return_code
        emit("error", message=f"OpenCode exited with code {return_code}")
        return return_code
    if not verify_project_workspace(project_workspace):
        return 1
    for svg in sorted((project_workspace / "svg_output").glob("*.svg")):
        emit("artifact", kind="svg", path=f"svg_output/{svg.name}")
    for pptx in sorted((project_workspace / "exports").glob("*.pptx")):
        emit("artifact", kind="pptx", path=f"exports/{pptx.name}")
    validation = _validate_revision(project_workspace, baseline, prompt, continue_mode, target_slide)
    emit("validation", **validation)
    if not validation["passed"]:
        emit("error", message=str(validation["message"]))
        return 1
    emit("status", status="succeeded")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        emit("error", message=f"Worker setup failed: {exc}")
        raise SystemExit(1)
