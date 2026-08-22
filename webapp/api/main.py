"""FastAPI entry point for the multi-user PPT Master web service."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import UUID

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import SessionLocal, get_db
from .deps import get_current_user, get_owned_project, require_admin, require_super_admin
from .models import (
    Artifact,
    ArtifactKind,
    AuthSession,
    Invitation,
    Job,
    JobEvent,
    JobStatus,
    ModelAccessPolicy,
    PageRefinementMessage,
    Project,
    ProjectMaterial,
    ProjectCreativeState,
    PromptSnippet,
    Provider,
    ProviderModel,
    SystemSetting,
    Template,
    TemplateStatus,
    User,
    UserRole,
)
from .schemas import (
    ArtifactOut,
    AdminUserCreateIn,
    AdminUserUpdateIn,
    AdminPromptSnippetCreateIn,
    AdminPromptSnippetUpdateIn,
    AdminTemplateUpdateIn,
    EditorSlideOut,
    EditorSlideSaveIn,
    InviteIn,
    InviteOut,
    JobCreateIn,
    JobEventOut,
    JobOut,
    LoginIn,
    ModelCatalogDefaultIn,
    ModelCatalogOut,
    ModelOut,
    PageRefinementIntentIn,
    PageRefinementIntentOut,
    PageRefinementMessageOut,
    PromptSnippetCreateIn,
    PromptSnippetOut,
    PromptSnippetUpdateIn,
    ProviderIn,
    ProviderModelIn,
    ProviderModelOut,
    ProviderModelUpdateIn,
    ProviderOut,
    ProviderUpdateIn,
    ProjectCreateIn,
    ProjectCreativeOutlineIn,
    ProjectCreativeStateOut,
    ProjectCreativeStateUpdateIn,
    ProjectOut,
    ProjectMaterialOut,
    ProjectUpdateIn,
    TemplateOut,
    TemplateRenameIn,
    RegisterIn,
    UserOut,
)
from .security import (
    hash_password,
    invitation_expiry,
    new_token,
    normalize_email,
    normalize_username,
    session_expiry,
    token_hash,
    verify_password,
)
from .provider_config import decrypt_api_key, encrypt_api_key, key_hint

settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
logger = logging.getLogger(__name__)


def _workspace_path(project: Project) -> Path:
    """Resolve a project workspace below the configured root."""

    root = settings.workspace_root.resolve()
    path = (root / project.workspace_relpath).resolve()
    if root not in path.parents:
        raise RuntimeError("Project workspace is outside WORKSPACE_ROOT")
    return path


def _template_workspace_path(template: Template) -> Path:
    """Resolve a template workspace below the configured user data root."""

    root = settings.workspace_root.resolve()
    path = (root / template.workspace_relpath).resolve()
    if root not in path.parents:
        raise RuntimeError("Template workspace is outside WORKSPACE_ROOT")
    return path


def _template_out(template: Template) -> TemplateOut:
    return TemplateOut(
        id=template.id,
        name=template.name,
        original_filename=template.original_filename,
        status=template.status,
        page_count=template.page_count,
        metadata=template.meta or {},
        error=template.error,
        created_at=template.created_at,
        updated_at=template.updated_at,
        scope=template.scope,
        is_active=template.is_active,
        sort_order=template.sort_order,
    )


def _prompt_snippet_out(snippet: PromptSnippet) -> PromptSnippetOut:
    """Convert one saved prompt into the public API representation."""

    return PromptSnippetOut(
        id=snippet.id,
        name=snippet.name,
        content=snippet.content,
        category=snippet.category,
        used_count=snippet.used_count,
        created_at=snippet.created_at,
        updated_at=snippet.updated_at,
        scope=snippet.scope,
        is_active=snippet.is_active,
        sort_order=snippet.sort_order,
    )


def _editor_artifact_path(project: Project, artifact: Artifact) -> Path:
    """Resolve one SVG artifact while preserving the project directory boundary."""

    project_root = _workspace_path(project)
    path = (project_root / artifact.relative_path).resolve()
    if project_root not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return path


def _validate_editor_svg(content: str) -> None:
    """Reject active or structurally invalid SVG before storing browser edits."""

    if re.search(r"<!DOCTYPE|<!ENTITY", content, flags=re.IGNORECASE):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 不支持 DTD 或实体声明")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"SVG 格式无效：{exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "编辑内容必须是 SVG 文档")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 不允许脚本或嵌入页面")
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (
                name == "href" and value.lstrip().lower().startswith("javascript:")
            ):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 包含不支持的活动内容")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        deletion_pending=user.deletion_requested_at is not None,
    )


def _creative_state_out(state: ProjectCreativeState) -> ProjectCreativeStateOut:
    """Convert one persisted creative workspace into its public shape."""

    return ProjectCreativeStateOut(
        project_id=state.project_id,
        stage=state.stage,
        requirements=state.requirements or {},
        outline=state.outline or [],
        notes_enabled=state.notes_enabled,
        selected_template_id=state.selected_template_id,
        updated_at=state.updated_at,
    )


def _outline_prompt(prompt: str, state: ProjectCreativeState | None) -> str:
    """Append a confirmed project outline to a first-generation instruction."""

    if state is None or not state.outline:
        return prompt
    requirements = state.requirements or {}
    lines = [prompt, "", "已确认的演示结构（必须遵循页面顺序并保留每页目标）："]
    for index, slide in enumerate(state.outline, start=1):
        if not isinstance(slide, dict):
            continue
        title = str(slide.get("title") or f"第 {index} 页").strip()
        purpose = str(slide.get("purpose") or "").strip()
        kind = str(slide.get("kind") or "内容页").strip()
        notes = str(slide.get("notes") or "").strip()
        lines.append(f"{index}. {title}（{kind}）")
        if purpose:
            lines.append(f"   目标：{purpose}")
        if notes:
            lines.append(f"   讲解重点：{notes}")
    if requirements:
        lines.append("")
        lines.append("需求补充：")
        for label, key in (("使用场景", "scenario"), ("目标受众", "audience"), ("整体风格", "style"), ("核心目标", "objective")):
            value = str(requirements.get(key) or "").strip()
            if value:
                lines.append(f"{label}：{value}")
    return "\n".join(lines)


def _refinement_history_prompt(messages: list[PageRefinementMessage]) -> str:
    """Render a bounded page conversation for the next refinement task."""

    if not messages:
        return ""
    lines = ["页面修改对话上下文（仅用于理解用户的连续要求，当前 PPT 文件是最终事实）："]
    for message in messages[-6:]:
        content = message.content.strip().replace("\x00", "")
        if not content:
            continue
        content = content[:900]
        label = "用户" if message.role == "user" else "助手"
        lines.append(f"{label}：{content}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _page_count_for_range(value: object) -> int:
    """Choose a deterministic target count inside the user's selected range."""

    text = str(value or "8-10 页").strip()
    match = re.search(r"(\d+)\s*[-至]\s*(\d+)", text)
    if match:
        return max(1, int(match.group(1)))
    above = re.search(r"(\d+)\s*页以上", text)
    return max(1, int(above.group(1))) if above else 8


def _fallback_outline(topic: str, count: int) -> list[dict[str, str]]:
    """Build a usable local outline when the configured model is unavailable."""

    title = topic.strip() or "本次演示主题"
    base = [
        (title, "建立汇报主题、对象与核心主张", "封面页", "开场说明本次演示要解决的问题。"),
        ("核心结论", "先给出观众需要记住的关键判断", "结论页", "用简洁语言说明结论与行动方向。"),
        ("背景与关键挑战", "解释为什么现在需要关注这个议题", "问题分析", "补充必要背景，避免陷入细节。"),
        ("现状与关键数据", "用事实建立对现状的共同认知", "数据图表", "说明数据来源与关键变化。"),
        ("重点分析", "用结构化信息支撑判断", "分析页", "说明证据、洞察与影响。"),
        ("方案设计", "说明可行路径与实施方法", "方案页", "把方案拆解成清晰步骤。"),
        ("案例与落地参考", "用案例验证方案的可行性", "案例页", "提炼可复用的经验与边界。"),
        ("价值与预期收益", "说明投入、收益和差异化价值", "价值页", "把价值转化成面向受众的收益。"),
        ("实施计划", "明确节奏、责任与关键里程碑", "行动计划", "列出近期可执行的行动项。"),
        ("风险与应对", "提前识别主要风险和缓解措施", "风险页", "说明风险等级与预案。"),
        ("下一步行动", "明确需要决策和支持的事项", "行动计划", "以明确的行动项结束演示。"),
    ]
    items = base[:max(1, count)]
    while len(items) < count:
        number = len(items) - len(base) + 1
        items.append((f"补充分析 {number:02d}", "补充支撑主题判断的关键信息", "内容页", "围绕主题补充必要信息。"))
    return [
        {"title": item[0], "purpose": item[1], "kind": item[2], "notes": item[3]}
        for item in items
    ]


def _parse_outline_response(raw: str, topic: str, count: int) -> list[dict[str, str]]:
    """Parse strict JSON or a JSON code block returned by an OpenAI-compatible model."""

    candidate = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 大纲")
    decoded = json.loads(candidate[start : end + 1])
    if not isinstance(decoded, list):
        raise ValueError("模型返回的大纲不是数组")
    valid: list[dict[str, str]] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        valid.append({
            "title": title[:160],
            "purpose": str(item.get("purpose") or "").strip()[:500],
            "kind": str(item.get("kind") or "内容页").strip()[:64],
            "notes": str(item.get("notes") or "").strip()[:2_000],
        })
    if not valid:
        raise ValueError("模型返回了空大纲")
    valid[0]["title"] = topic[:160]
    fallback = _fallback_outline(topic, count)
    return (valid[:count] + fallback[len(valid):count])[:count]


def _provider_request(
    provider_url: str,
    api_key: str,
    model_id: str,
    prompt: str,
    system_prompt: str = "你是专业的演示文稿策划师，只返回严格 JSON 数组，不要 Markdown。",
) -> str:
    """Perform one bounded OpenAI-compatible request without adding a runtime dependency."""

    endpoint = provider_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    request = UrlRequest(endpoint, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, method="POST")
    with urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
    if not isinstance(content, str):
        raise ValueError("模型响应缺少 content")
    return content


async def _active_model_credentials(db: AsyncSession) -> tuple[str, str, str] | None:
    """Resolve the administrator-selected model without exposing provider secrets."""

    active = next((item for item in await _model_catalog(db) if item.is_available and item.is_default), None)
    if not active:
        return None
    model_id = active.model_id
    provider_slug, _, raw_model = model_id.partition("/")
    if provider_slug in {"aihub", "deepseek"}:
        api_key = (settings.aihub_api_key if provider_slug == "aihub" else settings.deepseek_api_key) or None
        provider_url = "https://aihub.dog/v1" if provider_slug == "aihub" else "https://api.deepseek.com"
        return (provider_url, api_key, raw_model) if api_key else None
    managed = (
        await db.execute(
            select(Provider, ProviderModel)
            .join(ProviderModel, ProviderModel.provider_id == Provider.id)
            .where(
                Provider.slug == provider_slug,
                ProviderModel.model_id == raw_model,
                Provider.is_active.is_(True),
                ProviderModel.is_active.is_(True),
            )
            .limit(1)
        )
    ).first()
    if not managed:
        return None
    provider, provider_model = managed
    try:
        api_key = decrypt_api_key(provider.api_key_ciphertext)
    except (HTTPException, RuntimeError) as exc:
        logger.warning("Managed model provider is unavailable: %s", exc)
        return None
    return provider.base_url, api_key, provider_model.model_id


async def _generate_outline_with_model(db: AsyncSession, requirements: dict, count: int) -> tuple[list[dict[str, str]], bool]:
    """Generate an outline and report whether the model path was used."""

    topic = str(requirements.get("topic") or "本次演示主题").strip()
    prompt = (
        f"请为主题“{topic}”设计恰好 {count} 页演示文稿大纲。\n"
        f"页数范围：{requirements.get('page_range') or '未指定'}。\n"
        f"使用场景：{requirements.get('scenario') or '业务汇报'}；目标受众：{requirements.get('audience') or '相关决策者'}；"
        f"整体风格：{requirements.get('style') or '专业、简洁'}；核心目标：{requirements.get('objective') or '讲清楚主题'}。\n"
        "返回 JSON 数组，每项字段为 title、purpose、kind、notes；第一项必须是主题页，页面数量必须严格一致。"
    )
    credentials = await _active_model_credentials(db)
    if not credentials:
        return _fallback_outline(topic, count), False
    provider_url, api_key, model_id = credentials
    try:
        raw = await asyncio.to_thread(_provider_request, provider_url, api_key, model_id, prompt)
        return _parse_outline_response(raw, topic, count), True
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError, HTTPException, RuntimeError) as exc:
        logger.warning("Outline model generation failed; using fallback: %s", exc)
        return _fallback_outline(topic, count), False


_REFINEMENT_OPERATION_WORDS = (
    "修改", "调整", "改成", "改为", "换成", "替换", "删除", "删掉", "移除", "增加", "添加",
    "补充", "缩小", "放大", "移动", "上移", "下移", "左移", "右移", "改色", "变成", "美化",
    "重排", "对齐", "加粗", "减小", "增大",
)
_REFINEMENT_TARGET_WORDS = (
    "标题", "文字", "文案", "内容", "图表", "图片", "页面", "这一页", "当前页", "布局", "颜色",
    "字号", "字体", "背景", "元素", "图形", "位置", "间距", "段落", "表格",
)


def _refinement_intent_fallback(message: str) -> dict[str, object]:
    """Prevent unsafe PPT jobs when the conversation model is unavailable."""

    text = message.strip()
    has_operation = any(word in text for word in _REFINEMENT_OPERATION_WORDS)
    has_target = any(word in text for word in _REFINEMENT_TARGET_WORDS)
    if has_operation and has_target:
        return {"action": "modify_current_slide", "confidence": 0.9, "normalized_request": text}
    if has_operation:
        return {
            "action": "ask_clarification",
            "confidence": 0.65,
            "clarification_question": "你希望修改当前页的哪一部分？请说明对象和期望效果。",
        }
    return {
        "action": "answer_only",
        "confidence": 0.2,
        "reply": "当前 AI 对话模型暂时不可用，暂不能完成自然对话；如需修改当前页，请描述具体对象和调整方式。",
    }


def _parse_refinement_intent_response(raw: str, message: str) -> dict[str, object]:
    """Parse and constrain the classifier's strict JSON response."""

    candidate = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON 意图")
    decoded = json.loads(candidate[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError("模型返回的意图不是对象")
    action = str(decoded.get("action") or "").strip()
    if action not in {"modify_current_slide", "ask_clarification", "answer_only", "unsupported"}:
        raise ValueError("模型返回了未知意图")
    try:
        confidence = min(1.0, max(0.0, float(decoded.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    normalized = str(decoded.get("normalized_request") or message).strip()[:4_000]
    reply = str(decoded.get("reply") or "").strip()[:800]
    question = str(decoded.get("clarification_question") or "").strip()[:800]
    if action == "modify_current_slide" and confidence < 0.8:
        action = "ask_clarification"
        question = question or "我不太确定你的修改目标。请说明要调整的页面元素和期望效果。"
    if action == "ask_clarification" and not question:
        question = "你希望修改当前页的哪一部分？请说明对象和期望效果。"
    if action == "answer_only" and not reply:
        reply = "我可以帮你讨论当前页面，也可以根据明确要求提交 PPT 修改。"
    if action == "unsupported" and not reply:
        reply = "当前 AI 页面助手只负责修改选中的这一页，请描述具体的页面调整要求。"
    return {
        "action": action,
        "confidence": confidence,
        "normalized_request": normalized,
        "reply": reply,
        "clarification_question": question,
    }


async def _classify_refinement_intent(
    db: AsyncSession,
    slide_title: str,
    message: str,
    history: list[PageRefinementMessage],
) -> dict[str, object]:
    """Route every page-chat message through the model when it is available."""

    fallback = _refinement_intent_fallback(message)
    credentials = await _active_model_credentials(db)
    if not credentials:
        return fallback
    history_prompt = _refinement_history_prompt(history)
    prompt = (
        "你是当前 PPT 页面助手，负责理解用户意图并进行自然对话。\n"
        "每一条输入都必须先进行语义判断，不要因为命中关键词就机械分类。你只负责意图路由和回复，不要执行 PPT 修改。\n"
        f"当前页面：{slide_title.strip()[:200]}\n"
        f"{history_prompt}\n"
        f"本轮用户输入：{message.strip()[:4_000]}\n\n"
        "action 只能是："
        "modify_current_slide（明确要求修改当前选中的这一页）、"
        "ask_clarification（可能想修改，但对象、范围或效果不清楚）、"
        "answer_only（问候、身份问题、功能咨询、闲聊或普通问题）、"
        "unsupported（要求修改其他页面、导出文件、管理项目或超出当前页助手能力）。"
        "只有用户明确表达页面修改目标和操作时，才能使用 modify_current_slide；"
        "不能因为用户提到 PPT、页面或某个元素就直接创建修改任务。"
        "对 answer_only 和 unsupported，reply 必须是结合当前助手身份和上下文生成的自然中文回复，"
        "不能声称已经修改或提交任务；对 ask_clarification，clarification_question 要明确追问缺失信息。"
        "confidence 为 0 到 1 的数字。只返回严格 JSON 对象，不要 Markdown。"
        "返回字段：action、confidence、normalized_request、reply、clarification_question。"
    )
    provider_url, api_key, model_id = credentials
    try:
        raw = await asyncio.to_thread(
            _provider_request,
            provider_url,
            api_key,
            model_id,
            prompt,
            "你是当前 PPT 页面助手。请先理解语义，再决定是否需要修改当前页；对普通对话要自然回答。只返回严格 JSON 对象，不执行任何 PPT 修改。",
        )
        return _parse_refinement_intent_response(raw, message)
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError, HTTPException, RuntimeError) as exc:
        logger.warning("Refinement intent classification failed; using fallback: %s", exc)
        return fallback


def _provider_out(provider: Provider, models: list[ProviderModel]) -> ProviderOut:
    return ProviderOut(
        id=provider.id, slug=provider.slug, display_name=provider.display_name,
        base_url=provider.base_url, api_key_hint=provider.api_key_hint, is_active=provider.is_active,
        models=[ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default) for model in models],
    )


async def _model_catalog(db: AsyncSession) -> list[ModelCatalogOut]:
    """Return the merged environment and administrator-managed model catalog."""

    default_setting = await db.get(SystemSetting, "default_model_id")
    if default_setting is not None:
        default_model = default_setting.value or None
    else:
        legacy_default = (
            await db.execute(
                select(Provider, ProviderModel)
                .join(ProviderModel, ProviderModel.provider_id == Provider.id)
                .where(ProviderModel.is_default.is_(True))
                .order_by(Provider.display_name, ProviderModel.display_name)
                .limit(1)
            )
        ).first()
        default_model = (
            f"{legacy_default[0].slug}/{legacy_default[1].model_id}"
            if legacy_default
            else settings.configured_model
        )

    catalog: list[ModelCatalogOut] = []
    known_model_ids: set[str] = set()
    for model_id in settings.configured_models:
        provider_slug = model_id.partition("/")[0]
        catalog.append(
            ModelCatalogOut(
                model_id=model_id,
                source="environment",
                provider_display_name=provider_slug,
                is_available=True,
                is_default=model_id == default_model,
            )
        )
        known_model_ids.add(model_id)

    managed = await db.execute(
        select(Provider, ProviderModel)
        .join(ProviderModel, ProviderModel.provider_id == Provider.id)
        .order_by(Provider.display_name, ProviderModel.display_name)
    )
    for provider, provider_model in managed.all():
        model_id = f"{provider.slug}/{provider_model.model_id}"
        if model_id in known_model_ids:
            continue
        catalog.append(
            ModelCatalogOut(
                model_id=model_id,
                source="managed",
                provider_id=provider.id,
                provider_display_name=provider.display_name,
                is_available=provider.is_active and provider_model.is_active,
                is_default=model_id == default_model,
            )
        )
        known_model_ids.add(model_id)
    if not any(item.is_default for item in catalog):
        active_model = next((item for item in catalog if item.is_available), None)
        if active_model:
            active_model.is_default = True
    return catalog


async def _set_default_model(db: AsyncSession, model_id: str | None) -> None:
    """Persist an explicit global default, including an explicit no-default state."""

    setting = await db.get(SystemSetting, "default_model_id")
    if setting is None:
        setting = SystemSetting(key="default_model_id", value=model_id or "")
        db.add(setting)
    else:
        setting.value = model_id or ""


async def _clear_default_if_selected(db: AsyncSession, model_ids: list[str]) -> None:
    """Clear the global default only when an operation makes it unavailable."""

    selected = set(model_ids)
    if any(item.is_default and item.model_id in selected for item in await _model_catalog(db)):
        await _set_default_model(db, None)


async def _ensure_models_are_idle(db: AsyncSession, model_ids: list[str]) -> None:
    """Prevent lifecycle changes that would invalidate a queued or running job."""

    if not model_ids:
        return
    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                Job.model.in_(model_ids),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该模型仍有排队或执行中的任务，任务结束后才能停用或删除。",
        )


def _project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _material_out(material: ProjectMaterial) -> ProjectMaterialOut:
    return ProjectMaterialOut(
        id=material.id,
        original_filename=material.original_filename,
        content_type=material.content_type,
        size_bytes=material.size_bytes,
        status=material.status,
        metadata=material.meta or {},
        error=material.error,
        created_at=material.created_at,
        updated_at=material.updated_at,
    )


_MATERIAL_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg", ".webp"}
_MATERIAL_MAX_BYTES = 100 * 1024 * 1024


def _material_path(project: Project, material: ProjectMaterial) -> Path:
    """Resolve a project material while preserving the project workspace boundary."""

    root = _workspace_path(project)
    path = (root / material.relative_path).resolve()
    if root not in path.parents:
        raise RuntimeError("Project material is outside WORKSPACE_ROOT")
    return path


def _material_prompt_context(materials: list[ProjectMaterial]) -> str:
    """Build a bounded source manifest for the OpenCode generation prompt."""

    if not materials:
        return ""
    lines = ["", "项目已上传以下创作材料。生成前请先读取材料清单，并将其作为事实来源；不要修改原始材料：", "材料目录：materials/"]
    for material in materials:
        lines.append(f"- {material.original_filename}（{material.content_type}，{material.size_bytes} bytes，路径：{material.relative_path}）")
    lines.append("若材料是 PDF、DOCX、PPTX 或表格，请使用可用的来源转换工具提取内容后再规划页面；图片作为参考附件使用。")
    return "\n".join(lines)


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        base_job_id=job.base_job_id,
        target_slide_number=job.target_slide_number,
        template_id=job.template_id,
        template_name=job.template_name,
        status=job.status,
        prompt=job.prompt,
        model=job.model,
        error=job.error,
        cancellation_requested=job.cancellation_requested,
        created_at=job.created_at,
    )


def _artifact_out(artifact: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=artifact.id,
        kind=artifact.kind.value,
        filename=Path(artifact.relative_path).name,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
    )


async def _get_project_job(
    project: Project,
    job_id: UUID,
    db: AsyncSession,
) -> Job:
    """Return a job only when it belongs to the authorized project."""

    job = (
        await db.execute(select(Job).where(Job.id == job_id, Job.project_id == project.id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


async def _bootstrap_admin() -> None:
    """Create the configured first administrator when it does not exist."""

    if not settings.admin_username or not settings.admin_email or not settings.admin_password:
        return
    username = normalize_username(settings.admin_username)
    email = normalize_email(settings.admin_email)
    async with SessionLocal() as db:
        existing = (
            await db.execute(
                select(User).where(or_(User.username == username, User.email == email))
            )
        ).scalar_one_or_none()
        if existing:
            return
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(settings.admin_password),
                display_name="Administrator",
                role=UserRole.SUPER_ADMIN,
            )
        )
        await db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize the workspace root and optional bootstrap account."""

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    await _bootstrap_admin()
    yield


app = FastAPI(
    title="PPT生成智能体 Web API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    """Return a dependency-free health response for Compose health checks."""

    return {"status": "ok", "environment": settings.app_env}


@app.post("/api/v1/auth/login", response_model=UserOut)
async def login(payload: LoginIn, response: Response, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Authenticate a user and set a revocable HttpOnly browser session."""

    username = normalize_username(payload.username)
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    raw_token = new_token()
    db.add(AuthSession(user_id=user.id, token_hash=token_hash(raw_token), expires_at=session_expiry()))
    await db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return _user_out(user)


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke every browser session for the authenticated user."""

    sessions = await db.execute(select(AuthSession).where(AuthSession.user_id == user.id))
    for session in sessions.scalars():
        await db.delete(session)
    await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@app.get("/api/v1/auth/me", response_model=UserOut)
async def current_user(user: User = Depends(get_current_user)) -> UserOut:
    """Return the active user profile."""

    return _user_out(user)


@app.post("/api/v1/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InviteIn,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    """Issue a one-time invitation token for a specific email address."""

    token = new_token()
    invitation = Invitation(
        email=normalize_email(str(payload.email)),
        token_hash=token_hash(token),
        created_by=admin.id,
        expires_at=invitation_expiry(),
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    return InviteOut(
        id=invitation.id,
        email=invitation.email,
        token=token,
        expires_at=invitation.expires_at,
    )


@app.post("/api/v1/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Redeem an invitation and create the invited user account."""

    now = datetime.now(UTC)
    stmt = (
        select(Invitation)
        .where(Invitation.token_hash == token_hash(payload.token))
        .where(Invitation.used_at.is_(None))
        .where(Invitation.expires_at > now)
        .with_for_update()
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if not invitation:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation is invalid or expired")
    username = normalize_username(payload.username)
    existing = (
        await db.execute(
            select(User).where(
                or_(User.email == invitation.email, User.username == username)
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "The invitation or username is already in use")
    user = User(
        username=username,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    await db.flush()
    invitation.used_by = user.id
    invitation.used_at = now
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@app.get("/api/v1/admin/users", response_model=list[UserOut])
async def admin_list_users(admin: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)) -> list[UserOut]:
    """List accounts for the administrator console."""

    del admin
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [_user_out(user) for user in users]


@app.post("/api/v1/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def admin_create_user(payload: AdminUserCreateIn, admin: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    """Create an account directly instead of issuing an invitation."""

    del admin
    username = normalize_username(payload.username)
    if payload.password != payload.password_confirmation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "两次输入的密码不一致")
    email = f"{username}@accounts.pptmaster.example"
    existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "账号已存在")
    user = User(
        username=username,
        email=email,
        display_name=username,
        password_hash=hash_password(payload.password),
        role=UserRole.MEMBER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


async def _ensure_admin_survives(target: User, db: AsyncSession, *, next_role: UserRole | None = None, next_active: bool | None = None, deleting: bool = False) -> None:
    removes_admin = target.role is UserRole.ADMIN and (deleting or next_role is UserRole.MEMBER or next_active is False)
    if not removes_admin:
        return
    active_admins = (await db.execute(select(func.count(User.id)).where(User.role == UserRole.ADMIN, User.is_active.is_(True), User.deletion_requested_at.is_(None)))).scalar_one()
    if active_admins <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "必须保留至少一个启用的管理员")


async def _purge_deleted_user(target: User, db: AsyncSession) -> None:
    """Delete a deactivated account and every record that references it."""

    workspace_root = settings.workspace_root.resolve()
    user_workspace = (workspace_root / str(target.id)).resolve()
    if workspace_root not in user_workspace.parents:
        raise RuntimeError("User workspace is outside WORKSPACE_ROOT")

    invitations = (
        await db.execute(
            select(Invitation).where(
                or_(
                    Invitation.created_by == target.id,
                    Invitation.used_by == target.id,
                )
            )
        )
    ).scalars().all()
    projects = (
        await db.execute(select(Project).where(Project.owner_id == target.id))
    ).scalars().all()
    for invitation in invitations:
        await db.delete(invitation)
    for project in projects:
        await db.delete(project)
    await db.delete(target)
    await db.commit()

    if user_workspace.exists():
        try:
            shutil.rmtree(user_workspace)
        except OSError:
            logger.exception("Failed to remove deleted user workspace: %s", user_workspace)


@app.patch("/api/v1/admin/users/{user_id}", response_model=UserOut)
async def admin_update_user(user_id: UUID, payload: AdminUserUpdateIn, admin: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    await _ensure_admin_survives(target, db, next_role=payload.role, next_active=payload.is_active)
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    await db.commit()
    await db.refresh(target)
    return _user_out(target)


@app.delete("/api/v1/admin/users/{user_id}", response_model=UserOut, status_code=status.HTTP_202_ACCEPTED)
async def admin_delete_user(user_id: UUID, admin: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    """Deactivate an account and request cancellation before deferred removal."""

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能删除当前登录的管理员")
    await _ensure_admin_survives(target, db, deleting=True)
    target.is_active = False
    target.deletion_requested_at = datetime.now(UTC)
    jobs = (await db.execute(select(Job).join(Project).where(Project.owner_id == target.id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))).scalars().all()
    for job in jobs:
        job.cancellation_requested = True
        if job.status is JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
    await db.commit()
    response = _user_out(target)
    if not jobs:
        await _purge_deleted_user(target, db)
    return response


@app.get("/api/v1/admin/providers", response_model=list[ProviderOut])
async def admin_list_providers(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ProviderOut]:
    del admin
    providers = (await db.execute(select(Provider).order_by(Provider.display_name))).scalars().all()
    models = (await db.execute(select(ProviderModel).order_by(ProviderModel.display_name))).scalars().all()
    by_provider: dict[UUID, list[ProviderModel]] = {}
    for model in models:
        by_provider.setdefault(model.provider_id, []).append(model)
    return [_provider_out(provider, by_provider.get(provider.id, [])) for provider in providers]


@app.post("/api/v1/admin/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def admin_create_provider(payload: ProviderIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderOut:
    del admin
    provider = Provider(slug=payload.slug, display_name=payload.display_name.strip(), base_url=payload.base_url.rstrip("/"), api_key_ciphertext=encrypt_api_key(payload.api_key), api_key_hint=key_hint(payload.api_key), is_active=payload.is_active)
    db.add(provider)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Provider 标识已存在") from exc
    await db.refresh(provider)
    return _provider_out(provider, [])


@app.patch("/api/v1/admin/providers/{provider_id}", response_model=ProviderOut)
async def admin_update_provider(provider_id: UUID, payload: ProviderUpdateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderOut:
    del admin
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    provider_models = (
        await db.execute(select(ProviderModel).where(ProviderModel.provider_id == provider.id))
    ).scalars().all()
    model_ids = [f"{provider.slug}/{item.model_id}" for item in provider_models]
    if payload.is_active is False and provider.is_active:
        await _ensure_models_are_idle(db, model_ids)
        await _clear_default_if_selected(db, model_ids)
    if payload.display_name is not None: provider.display_name = payload.display_name.strip()
    if payload.base_url is not None: provider.base_url = payload.base_url.rstrip("/")
    if payload.is_active is not None: provider.is_active = payload.is_active
    if payload.api_key is not None:
        provider.api_key_ciphertext, provider.api_key_hint = encrypt_api_key(payload.api_key), key_hint(payload.api_key)
    await db.commit()
    return _provider_out(provider, provider_models)


@app.delete("/api/v1/admin/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_provider(provider_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    """Delete one managed provider after its active jobs have completed."""

    del admin
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    provider_models = (
        await db.execute(select(ProviderModel).where(ProviderModel.provider_id == provider.id))
    ).scalars().all()
    model_ids = [f"{provider.slug}/{item.model_id}" for item in provider_models]
    await _ensure_models_are_idle(db, model_ids)
    await _clear_default_if_selected(db, model_ids)
    policies = (
        await db.execute(
            select(ModelAccessPolicy).where(ModelAccessPolicy.model_id.in_(model_ids))
        )
    ).scalars().all()
    for policy in policies:
        await db.delete(policy)
    await db.delete(provider)
    await db.commit()


@app.post("/api/v1/admin/providers/{provider_id}/models", response_model=ProviderModelOut, status_code=status.HTTP_201_CREATED)
async def admin_create_model(provider_id: UUID, payload: ProviderModelIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderModelOut:
    del admin
    if not await db.get(Provider, provider_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    model = ProviderModel(
        provider_id=provider_id,
        model_id=payload.model_id.strip(),
        display_name=payload.display_name.strip(),
        is_active=payload.is_active,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default)


@app.patch("/api/v1/admin/models/{model_id}", response_model=ProviderModelOut)
async def admin_update_model(model_id: UUID, payload: ProviderModelUpdateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderModelOut:
    del admin
    model = await db.get(ProviderModel, model_id)
    if not model: raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    provider = await db.get(Provider, model.provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    catalog_id = f"{provider.slug}/{model.model_id}"
    if payload.is_active is False and model.is_active:
        await _ensure_models_are_idle(db, [catalog_id])
        await _clear_default_if_selected(db, [catalog_id])
    if payload.display_name is not None: model.display_name = payload.display_name.strip()
    if payload.is_active is not None: model.is_active = payload.is_active
    await db.commit()
    return ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default)


@app.delete("/api/v1/admin/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_model(model_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    """Delete one managed model after its active jobs have completed."""

    del admin
    model = await db.get(ProviderModel, model_id)
    if not model:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    provider = await db.get(Provider, model.provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    catalog_id = f"{provider.slug}/{model.model_id}"
    await _ensure_models_are_idle(db, [catalog_id])
    await _clear_default_if_selected(db, [catalog_id])
    policy = await db.get(ModelAccessPolicy, catalog_id)
    if policy:
        await db.delete(policy)
    await db.delete(model)
    await db.commit()


@app.get("/api/v1/admin/model-catalog", response_model=list[ModelCatalogOut])
async def admin_list_model_catalog(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ModelCatalogOut]:
    """List every model the platform knows, including disabled entries."""

    del admin
    return await _model_catalog(db)


@app.patch("/api/v1/admin/model-catalog/default", response_model=list[ModelCatalogOut])
async def admin_update_default_model(payload: ModelCatalogDefaultIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ModelCatalogOut]:
    """Set the single platform-wide model used by every generation job."""

    del admin
    catalog = await _model_catalog(db)
    entry = next((item for item in catalog if item.model_id == payload.model_id), None)
    if not entry or not entry.is_available:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能激活当前可用的模型")
    await _set_default_model(db, payload.model_id)
    await db.commit()
    return await _model_catalog(db)


@app.get("/api/v1/projects", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    """List projects that belong to the authenticated user."""

    stmt = select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())
    projects = (await db.execute(stmt)).scalars().all()
    return [_project_out(project) for project in projects]


@app.post("/api/v1/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a user-owned project directory and database record."""

    project = Project(owner_id=user.id, title=payload.title.strip(), workspace_relpath="pending")
    db.add(project)
    await db.flush()
    project.workspace_relpath = f"{user.id}/{project.id}"
    _workspace_path(project).mkdir(parents=True, exist_ok=False)
    await db.commit()
    await db.refresh(project)
    return _project_out(project)


@app.get("/api/v1/projects/{project_id}/materials", response_model=list[ProjectMaterialOut])
async def list_project_materials(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectMaterialOut]:
    """List source materials attached to one owned project."""

    materials = (
        await db.execute(
            select(ProjectMaterial)
            .where(ProjectMaterial.project_id == project.id)
            .order_by(ProjectMaterial.created_at.asc())
        )
    ).scalars().all()
    return [_material_out(material) for material in materials]


@app.post("/api/v1/projects/{project_id}/materials", response_model=ProjectMaterialOut, status_code=status.HTTP_201_CREATED)
async def upload_project_material(
    file: UploadFile = File(...),
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectMaterialOut:
    """Store one source file in a project-local materials directory."""

    original_filename = Path(file.filename or "material").name
    suffix = Path(original_filename).suffix.lower()
    if suffix not in _MATERIAL_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "暂不支持该材料格式，请上传 PDF、DOCX、PPTX、表格、文本或图片。")
    material = ProjectMaterial(
        project_id=project.id,
        original_filename=original_filename[:255],
        relative_path="pending",
        content_type=(file.content_type or "application/octet-stream")[:255],
        status="processing",
        metadata={"extension": suffix, "parse_mode": "deferred"},
    )
    db.add(material)
    await db.flush()
    material.relative_path = f"materials/{material.id}{suffix}"
    destination = _material_path(project, material)
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > _MATERIAL_MAX_BYTES:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "单个材料不能超过 100 MB。")
                output.write(chunk)
        if suffix == ".pptx":
            try:
                with zipfile.ZipFile(destination) as archive:
                    names = set(archive.namelist())
                    if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                        raise ValueError("不是有效的 PPTX 演示文稿")
            except (zipfile.BadZipFile, OSError, ValueError) as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"PPTX 材料无效：{exc}") from exc
        material.size_bytes = total
        material.status = "ready"
        metadata = dict(material.meta or {})
        metadata.update({"stored": True, "parse_message": "材料已保存，生成任务会在工作区内完成来源转换。"})
        material.meta = metadata
        project.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(material)
        return _material_out(material)
    except Exception:
        await db.rollback()
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.delete("/api/v1/projects/{project_id}/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_material(
    material_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove one source file from its owning project."""

    material = (
        await db.execute(
            select(ProjectMaterial).where(ProjectMaterial.id == material_id, ProjectMaterial.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not material:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料不存在")
    path = _material_path(project, material)
    await db.delete(material)
    project.updated_at = datetime.now(UTC)
    await db.commit()
    path.unlink(missing_ok=True)


@app.get("/api/v1/projects/{project_id}/materials/{material_id}/download")
async def download_project_material(
    material_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
):
    """Download an authorized project material for inspection."""

    material = (
        await db.execute(
            select(ProjectMaterial).where(ProjectMaterial.id == material_id, ProjectMaterial.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not material:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料不存在")
    path = _material_path(project, material)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料文件不存在")
    return FileResponse(path, media_type=material.content_type, filename=material.original_filename)


@app.get("/api/v1/templates", response_model=list[TemplateOut])
async def list_templates(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TemplateOut]:
    """List personal templates plus active platform templates."""

    templates = (
        await db.execute(
            select(Template)
            .where(or_(Template.owner_id == user.id, and_(Template.scope == "system", Template.is_active.is_(True))))
            .order_by(Template.scope.desc(), Template.sort_order, Template.updated_at.desc())
        )
    ).scalars().all()
    return [_template_out(template) for template in templates]


@app.post("/api/v1/templates/import", response_model=TemplateOut, status_code=status.HTTP_202_ACCEPTED)
async def import_template(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Store one PPTX and queue deterministic template analysis."""

    original_filename = Path(file.filename or "uploaded.pptx").name
    if not original_filename.lower().endswith(".pptx"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "模板文件必须是 .pptx 格式")
    template = Template(
        owner_id=user.id,
        name=Path(original_filename).stem[:160] or "未命名模板",
        original_filename=original_filename[:255],
        workspace_relpath="pending",
        status=TemplateStatus.ANALYZING.value,
        meta={},
    )
    db.add(template)
    await db.flush()
    template.workspace_relpath = f"{user.id}/templates/{template.id}"
    workspace = _template_workspace_path(template)
    workspace.mkdir(parents=True, exist_ok=False)
    source = workspace / "source.pptx"
    max_bytes = 100 * 1024 * 1024
    total = 0
    try:
        with source.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "模板文件不能超过 100 MB")
                output.write(chunk)
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    finally:
        await file.close()
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "上传文件不是有效的 PPTX 演示文稿")
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "上传文件不是有效的 PPTX 演示文稿") from exc
    await db.commit()
    await db.refresh(template)
    from runner.celery_app import celery_app

    celery_app.send_task("runner.import_template", args=[str(template.id)])
    return _template_out(template)


@app.get("/api/v1/admin/system-templates", response_model=list[TemplateOut])
async def admin_list_system_templates(
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TemplateOut]:
    """List platform templates, including disabled entries."""

    del admin
    templates = (await db.execute(select(Template).where(Template.scope == "system").order_by(Template.sort_order, Template.updated_at.desc()))).scalars().all()
    return [_template_out(template) for template in templates]


@app.post("/api/v1/admin/system-templates/import", response_model=TemplateOut, status_code=status.HTTP_202_ACCEPTED)
async def admin_import_system_template(
    file: UploadFile = File(...),
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Import a PPTX into the shared template library through the normal parser."""

    created = await import_template(file, admin, db)
    template = await db.get(Template, created.id)
    if template is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "系统模板创建失败")
    template.scope = "system"
    template.is_active = True
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


@app.patch("/api/v1/admin/system-templates/{template_id}", response_model=TemplateOut)
async def admin_update_system_template(
    template_id: UUID,
    payload: AdminTemplateUpdateIn,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Update the visible name, ordering, and availability of a system template."""

    del admin
    template = (await db.execute(select(Template).where(Template.id == template_id, Template.scope == "system"))).scalar_one_or_none()
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统模板不存在")
    if payload.name is not None:
        template.name = payload.name.strip()
    if payload.is_active is not None:
        template.is_active = payload.is_active
    if payload.sort_order is not None:
        template.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


@app.delete("/api/v1/admin/system-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_system_template(
    template_id: UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a platform template without modifying prior project records."""

    del admin
    template = (await db.execute(select(Template).where(Template.id == template_id, Template.scope == "system"))).scalar_one_or_none()
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统模板不存在")
    workspace = _template_workspace_path(template)
    await db.delete(template)
    await db.commit()
    shutil.rmtree(workspace, ignore_errors=True)


async def _get_owned_template(template_id: UUID, user: User, db: AsyncSession) -> Template:
    template = (
        await db.execute(select(Template).where(
            Template.id == template_id,
            or_(Template.owner_id == user.id, and_(Template.scope == "system", Template.is_active.is_(True))),
        ))
    ).scalar_one_or_none()
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板不存在")
    return template


async def _get_personal_template(template_id: UUID, user: User, db: AsyncSession) -> Template:
    template = (await db.execute(select(Template).where(Template.id == template_id, Template.owner_id == user.id, Template.scope == "user"))).scalar_one_or_none()
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "个人模板不存在")
    return template


@app.get("/api/v1/templates/{template_id}/files/{file_path:path}")
async def download_template_file(
    template_id: UUID,
    file_path: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve a preview or source file from an authorized template workspace."""

    template = await _get_owned_template(template_id, user, db)
    root = _template_workspace_path(template)
    path = (root / file_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板文件不存在")
    media_type = "image/svg+xml" if path.suffix.lower() == ".svg" else None
    return FileResponse(path, media_type=media_type)


@app.post("/api/v1/templates/{template_id}/retry", response_model=TemplateOut, status_code=status.HTTP_202_ACCEPTED)
async def retry_template_import(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Requeue an interrupted template import and reset its visible progress."""

    template = await _get_personal_template(template_id, user, db)
    metadata = dict(template.meta or {})
    metadata["progress"] = {
        "stage": "queued",
        "message": "已重新加入模板解析队列",
        "logs": ["用户请求重新分析模板"],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    template.meta = metadata
    template.status = TemplateStatus.ANALYZING.value
    template.error = None
    await db.commit()
    await db.refresh(template)
    from runner.celery_app import celery_app

    celery_app.send_task("runner.import_template", args=[str(template.id)])
    return _template_out(template)


@app.patch("/api/v1/templates/{template_id}", response_model=TemplateOut)
async def rename_template(
    template_id: UUID,
    payload: TemplateRenameIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TemplateOut:
    """Rename one personal template without changing its imported files."""

    template = await _get_personal_template(template_id, user, db)
    template.name = payload.name.strip()
    await db.commit()
    await db.refresh(template)
    return _template_out(template)


@app.delete("/api/v1/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete one owned template and its imported files."""

    template = await _get_personal_template(template_id, user, db)
    active_job = (
        await db.execute(
            select(Job.id)
            .join(Project)
            .where(
                Project.owner_id == user.id,
                Job.template_id == template.id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该模板正被任务使用，任务结束后才能删除。",
        )
    workspace = _template_workspace_path(template)
    await db.delete(template)
    await db.commit()
    shutil.rmtree(workspace, ignore_errors=True)


async def _get_owned_prompt_snippet(
    snippet_id: UUID,
    user: User,
    db: AsyncSession,
) -> PromptSnippet:
    """Load one saved prompt only when it belongs to the signed-in user."""

    snippet = (
        await db.execute(
            select(PromptSnippet).where(
                PromptSnippet.id == snippet_id,
                PromptSnippet.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "提示词不存在")
    return snippet


async def _get_visible_prompt_snippet(
    snippet_id: UUID,
    user: User,
    db: AsyncSession,
) -> PromptSnippet:
    snippet = (
        await db.execute(
            select(PromptSnippet).where(
                PromptSnippet.id == snippet_id,
                or_(
                    PromptSnippet.owner_id == user.id,
                    and_(PromptSnippet.scope == "system", PromptSnippet.is_active.is_(True)),
                ),
            )
        )
    ).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "提示词不存在")
    return snippet


@app.get("/api/v1/prompt-snippets", response_model=list[PromptSnippetOut])
async def list_prompt_snippets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PromptSnippetOut]:
    """List personal prompts plus active platform prompts."""

    snippets = (
        await db.execute(
            select(PromptSnippet)
            .where(
                or_(
                    PromptSnippet.owner_id == user.id,
                    and_(PromptSnippet.scope == "system", PromptSnippet.is_active.is_(True)),
                )
            )
            .order_by(PromptSnippet.scope.desc(), PromptSnippet.sort_order, PromptSnippet.updated_at.desc())
        )
    ).scalars().all()
    return [_prompt_snippet_out(snippet) for snippet in snippets]


@app.post(
    "/api/v1/prompt-snippets",
    response_model=PromptSnippetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_snippet(
    payload: PromptSnippetCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptSnippetOut:
    """Save a prompt for reuse in future presentation work."""

    snippet = PromptSnippet(
        owner_id=user.id,
        name=payload.name.strip(),
        content=payload.content.strip(),
        category=payload.category.strip(),
    )
    db.add(snippet)
    await db.commit()
    await db.refresh(snippet)
    return _prompt_snippet_out(snippet)


@app.get("/api/v1/admin/system-prompts", response_model=list[PromptSnippetOut])
async def admin_list_system_prompts(
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> list[PromptSnippetOut]:
    """List platform prompts, including disabled entries."""

    del admin
    snippets = (await db.execute(select(PromptSnippet).where(PromptSnippet.scope == "system").order_by(PromptSnippet.sort_order, PromptSnippet.updated_at.desc()))).scalars().all()
    return [_prompt_snippet_out(snippet) for snippet in snippets]


@app.post("/api/v1/admin/system-prompts", response_model=PromptSnippetOut, status_code=status.HTTP_201_CREATED)
async def admin_create_system_prompt(
    payload: AdminPromptSnippetCreateIn,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> PromptSnippetOut:
    """Create one reusable prompt visible to every active user."""

    snippet = PromptSnippet(
        owner_id=admin.id,
        scope="system",
        name=payload.name.strip(),
        content=payload.content.strip(),
        category=payload.category.strip(),
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(snippet)
    await db.commit()
    await db.refresh(snippet)
    return _prompt_snippet_out(snippet)


@app.patch("/api/v1/admin/system-prompts/{snippet_id}", response_model=PromptSnippetOut)
async def admin_update_system_prompt(
    snippet_id: UUID,
    payload: AdminPromptSnippetUpdateIn,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> PromptSnippetOut:
    """Update a reusable prompt in the platform library."""

    del admin
    snippet = (await db.execute(select(PromptSnippet).where(PromptSnippet.id == snippet_id, PromptSnippet.scope == "system"))).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统提示词不存在")
    for field in ("name", "content", "category"):
        value = getattr(payload, field)
        if value is not None:
            setattr(snippet, field, value.strip())
    if payload.is_active is not None:
        snippet.is_active = payload.is_active
    if payload.sort_order is not None:
        snippet.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(snippet)
    return _prompt_snippet_out(snippet)


@app.delete("/api/v1/admin/system-prompts/{snippet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_system_prompt(
    snippet_id: UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete one system prompt from the platform library."""

    del admin
    snippet = (await db.execute(select(PromptSnippet).where(PromptSnippet.id == snippet_id, PromptSnippet.scope == "system"))).scalar_one_or_none()
    if not snippet:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统提示词不存在")
    await db.delete(snippet)
    await db.commit()


@app.patch("/api/v1/prompt-snippets/{snippet_id}", response_model=PromptSnippetOut)
async def update_prompt_snippet(
    snippet_id: UUID,
    payload: PromptSnippetUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptSnippetOut:
    """Update one owned reusable prompt."""

    snippet = await _get_owned_prompt_snippet(snippet_id, user, db)
    if payload.name is not None:
        snippet.name = payload.name.strip()
    if payload.content is not None:
        snippet.content = payload.content.strip()
    if payload.category is not None:
        snippet.category = payload.category.strip()
    await db.commit()
    await db.refresh(snippet)
    return _prompt_snippet_out(snippet)


@app.post("/api/v1/prompt-snippets/{snippet_id}/use", response_model=PromptSnippetOut)
async def use_prompt_snippet(
    snippet_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PromptSnippetOut:
    """Record one prompt insertion without exposing another user's prompt."""

    snippet = await _get_visible_prompt_snippet(snippet_id, user, db)
    snippet.used_count += 1
    await db.commit()
    await db.refresh(snippet)
    return _prompt_snippet_out(snippet)


@app.delete("/api/v1/prompt-snippets/{snippet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_snippet(
    snippet_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an owned saved prompt."""

    snippet = await _get_owned_prompt_snippet(snippet_id, user, db)
    await db.delete(snippet)
    await db.commit()


@app.get("/api/v1/projects/{project_id}", response_model=ProjectOut)
async def get_project(project: Project = Depends(get_owned_project)) -> ProjectOut:
    """Return a project owned by the authenticated user."""

    return _project_out(project)


@app.patch("/api/v1/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    payload: ProjectUpdateIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Rename one project without affecting its jobs or artifacts."""

    project.title = payload.title.strip()
    project.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(project)
    return _project_out(project)


@app.post("/api/v1/projects/{project_id}/duplicate", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def duplicate_project(
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a new draft with copied creative inputs and no generated artifacts."""

    duplicate = Project(owner_id=user.id, title=f"{project.title} - 副本"[:160], workspace_relpath="pending")
    db.add(duplicate)
    await db.flush()
    duplicate.workspace_relpath = f"{user.id}/{duplicate.id}"
    _workspace_path(duplicate).mkdir(parents=True, exist_ok=False)
    state = await db.get(ProjectCreativeState, project.id)
    if state:
        db.add(
            ProjectCreativeState(
                project_id=duplicate.id,
                stage=state.stage,
                requirements=dict(state.requirements or {}),
                outline=list(state.outline or []),
                notes_enabled=state.notes_enabled,
                selected_template_id=state.selected_template_id,
            )
        )
    await db.commit()
    await db.refresh(duplicate)
    return _project_out(duplicate)


@app.get("/api/v1/projects/{project_id}/creative-state", response_model=ProjectCreativeStateOut)
async def get_project_creative_state(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectCreativeStateOut:
    """Return persisted requirements and outline, or a fresh workspace state."""

    state = await db.get(ProjectCreativeState, project.id)
    if state is None:
        state = ProjectCreativeState(project_id=project.id)
        db.add(state)
        await db.commit()
        await db.refresh(state)
    return _creative_state_out(state)


@app.put("/api/v1/projects/{project_id}/creative-state", response_model=ProjectCreativeStateOut)
async def update_project_creative_state(
    payload: ProjectCreativeStateUpdateIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectCreativeStateOut:
    """Persist an explicitly confirmed requirement, outline, or template choice."""

    state = await db.get(ProjectCreativeState, project.id)
    if state is None:
        state = ProjectCreativeState(project_id=project.id)
        db.add(state)
    if payload.stage is not None:
        if payload.stage not in {"requirements", "outline", "template", "generating", "preview"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "未知的创作阶段")
        state.stage = payload.stage
    if payload.requirements is not None:
        state.requirements = payload.requirements
    if payload.outline is not None:
        state.outline = [slide.model_dump() for slide in payload.outline]
    if payload.notes_enabled is not None:
        state.notes_enabled = payload.notes_enabled
    if "selected_template_id" in payload.model_fields_set:
        state.selected_template_id = payload.selected_template_id
    project.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(state)
    return _creative_state_out(state)


@app.post("/api/v1/projects/{project_id}/creative-outline", response_model=ProjectCreativeStateOut)
async def generate_project_creative_outline(
    payload: ProjectCreativeOutlineIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectCreativeStateOut:
    """Generate a page-count-aware outline, with a deterministic local fallback."""

    state = await db.get(ProjectCreativeState, project.id)
    if state is None:
        state = ProjectCreativeState(project_id=project.id)
        db.add(state)
        await db.flush()
    if payload.requirements is not None:
        state.requirements = payload.requirements
    requirements = dict(state.requirements or {})
    topic = str(requirements.get("topic") or project.title).strip()
    count = _page_count_for_range(requirements.get("page_range"))
    outline, _ = await _generate_outline_with_model(db, requirements, count)
    state.outline = outline
    state.stage = "outline"
    project.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(state)
    return _creative_state_out(state)


@app.delete("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an owned project, its jobs, and its isolated workspace."""

    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                and_(
                    Job.project_id == project.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "当前对话仍有任务在执行，请等待完成后再删除。",
        )

    workspace_path = _workspace_path(project)
    await db.delete(project)
    await db.commit()
    if workspace_path.exists():
        try:
            shutil.rmtree(workspace_path)
        except OSError:
            logger.exception("Failed to remove deleted project workspace: %s", workspace_path)


@app.post(
    "/api/v1/projects/{project_id}/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job(
    payload: JobCreateIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Queue a project generation job for the isolated runner service."""

    active_model = next(
        (item for item in await _model_catalog(db) if item.is_available and item.is_default),
        None,
    )
    if not active_model:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OpenCode 尚未配置模型。请先设置 PPTMASTER_DEFAULT_MODEL 及对应提供方的凭据。",
        )
    if payload.model and payload.model.strip() != active_model.model_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "模型由管理员统一配置，不能在任务中单独选择")
    model = active_model.model_id

    # Lock the conversation row while choosing its base revision. This keeps
    # two browser tabs from starting concurrent edits against the same version.
    locked_project = (
        await db.execute(select(Project).where(Project.id == project.id).with_for_update())
    ).scalar_one()
    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                and_(
                    Job.project_id == locked_project.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "当前对话仍有任务在执行，请等待完成后再继续修改。",
        )
    if payload.base_job_id:
        base_job = (
            await db.execute(
                select(Job).where(
                    Job.id == payload.base_job_id,
                    Job.project_id == locked_project.id,
                    Job.status.in_([JobStatus.SUCCEEDED, JobStatus.FAILED]),
                )
            )
        ).scalar_one_or_none()
        if not base_job:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "要继续的任务不存在、尚未结束或不属于当前项目。",
            )
    else:
        base_job = (
            await db.execute(
                select(Job)
                .where(Job.project_id == locked_project.id, Job.status == JobStatus.SUCCEEDED)
                .order_by(Job.finished_at.desc(), Job.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if payload.target_slide_number is not None and base_job is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "页面精修必须基于当前已完成的演示文稿。",
        )
    template: Template | None = None
    template_root: str | None = None
    if payload.template_id:
        if base_job:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "模板只能在新建演示文稿时选择。",
            )
        template = (
            await db.execute(
                select(Template).where(
                    Template.id == payload.template_id,
                    or_(Template.owner_id == user.id, and_(Template.scope == "system", Template.is_active.is_(True))),
                    Template.status == TemplateStatus.READY.value,
                )
            )
        ).scalar_one_or_none()
        if not template:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "所选模板不存在或尚未准备完成。")
        template_root = str((template.meta or {}).get("template_root") or "").strip()
        if not template_root:
            raise HTTPException(status.HTTP_409_CONFLICT, "所选模板缺少可用的模板工作区。")
    creative_state = await db.get(ProjectCreativeState, locked_project.id)
    job_prompt = _outline_prompt(payload.prompt.strip(), creative_state) if base_job is None else payload.prompt.strip()
    if payload.target_slide_number is not None:
        history_stmt = (
            select(PageRefinementMessage)
            .where(
                PageRefinementMessage.project_id == locked_project.id,
                PageRefinementMessage.slide_number == payload.target_slide_number,
            )
            .order_by(PageRefinementMessage.message_order.desc())
            .limit(8)
        )
        history = list((await db.execute(history_stmt)).scalars().all())
        history.reverse()
        history_prompt = _refinement_history_prompt(history)
        if history_prompt:
            job_prompt = f"{job_prompt}\n\n{history_prompt}"
    project_materials = (
        await db.execute(
            select(ProjectMaterial)
            .where(ProjectMaterial.project_id == locked_project.id, ProjectMaterial.status == "ready")
            .order_by(ProjectMaterial.created_at.asc())
        )
    ).scalars().all()
    job_prompt = f"{job_prompt}{_material_prompt_context(project_materials)}"
    if len(job_prompt) > 20_000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "确认的大纲内容过长，请精简后再生成")
    job = Job(
        project_id=locked_project.id,
        base_job_id=base_job.id if base_job else None,
        target_slide_number=payload.target_slide_number,
        template_id=template.id if template else None,
        template_name=template.name if template else None,
        template_workspace_relpath=template.workspace_relpath if template else None,
        template_root=template_root,
        submitted_by=user.id,
        prompt=job_prompt,
        model=model,
    )
    locked_project.updated_at = datetime.now(UTC)
    db.add(job)
    await db.flush()
    if payload.target_slide_number is not None and payload.conversation_message:
        db.add(
            PageRefinementMessage(
                project_id=locked_project.id,
                job_id=job.id,
                slide_number=payload.target_slide_number,
                role="user",
                content=payload.conversation_message.strip(),
                client_message_id=payload.client_message_id,
            )
        )
    await db.commit()
    await db.refresh(job)
    from runner.celery_app import celery_app

    celery_app.send_task("runner.execute_job", args=[str(job.id)])
    return _job_out(job)


@app.post(
    "/api/v1/projects/{project_id}/refinement-intent",
    response_model=PageRefinementIntentOut,
)
async def classify_refinement_intent(
    payload: PageRefinementIntentIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> PageRefinementIntentOut:
    """Classify a page-chat message before it can create a PPT modification job."""

    if payload.client_message_id:
        existing_user = (
            await db.execute(
                select(PageRefinementMessage)
                .where(
                    PageRefinementMessage.project_id == project.id,
                    PageRefinementMessage.slide_number == payload.slide_number,
                    PageRefinementMessage.client_message_id == payload.client_message_id,
                    PageRefinementMessage.role == "user",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_user:
            existing_reply = (
                await db.execute(
                    select(PageRefinementMessage)
                    .where(
                    PageRefinementMessage.project_id == project.id,
                    PageRefinementMessage.slide_number == payload.slide_number,
                    PageRefinementMessage.message_order > existing_user.message_order,
                    PageRefinementMessage.role == "assistant",
                )
                    .order_by(PageRefinementMessage.message_order)
                    .limit(1)
                )
            ).scalar_one_or_none()
            return PageRefinementIntentOut(
                action="answer_only",
                confidence=1,
                normalized_request=payload.message.strip(),
                reply=existing_reply.content if existing_reply else "这条消息已经收到。",
            )

    history_stmt = (
        select(PageRefinementMessage)
        .where(
            PageRefinementMessage.project_id == project.id,
            PageRefinementMessage.slide_number == payload.slide_number,
        )
        .order_by(PageRefinementMessage.message_order.desc())
        .limit(6)
    )
    history = list((await db.execute(history_stmt)).scalars().all())
    history.reverse()
    result = await _classify_refinement_intent(db, payload.slide_title, payload.message, history)
    action = str(result.get("action") or "answer_only")
    reply = str(result.get("reply") or "").strip()
    question = str(result.get("clarification_question") or "").strip()
    assistant_content = reply if action in {"answer_only", "unsupported"} else question
    if action != "modify_current_slide":
        db.add(PageRefinementMessage(
            project_id=project.id,
            slide_number=payload.slide_number,
            role="user",
            content=payload.message.strip(),
            client_message_id=payload.client_message_id,
        ))
        await db.flush()
        db.add(PageRefinementMessage(
            project_id=project.id,
            slide_number=payload.slide_number,
            role="assistant",
            content=assistant_content or "请说明你想如何修改当前页面。",
        ))
        await db.commit()
    return PageRefinementIntentOut(
        action=action,
        confidence=float(result.get("confidence") or 0),
        normalized_request=str(result.get("normalized_request") or payload.message).strip(),
        reply=reply,
        clarification_question=question,
    )


@app.get(
    "/api/v1/projects/{project_id}/refinement-messages",
    response_model=list[PageRefinementMessageOut],
)
async def list_refinement_messages(
    slide_number: int,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[PageRefinementMessageOut]:
    """Return the persisted AI conversation for one project page."""

    if slide_number < 1 or slide_number > 999:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "页面编号无效")
    stmt = (
        select(PageRefinementMessage)
        .where(
            PageRefinementMessage.project_id == project.id,
            PageRefinementMessage.slide_number == slide_number,
        )
        .order_by(PageRefinementMessage.message_order)
    )
    messages = (await db.execute(stmt)).scalars().all()
    return [
        PageRefinementMessageOut(
            id=message.id,
            job_id=message.job_id,
            slide_number=message.slide_number,
            role=message.role,
            content=message.content,
            message_order=message.message_order,
            created_at=message.created_at,
        )
        for message in messages
    ]


@app.get("/api/v1/models", response_model=list[ModelOut])
async def list_models(user: User = Depends(get_current_user)) -> list[ModelOut]:
    """List the server-approved OpenCode models available to the signed-in user."""

    del user
    async with SessionLocal() as db:
        catalog = await _model_catalog(db)
    return [
        ModelOut(id=item.model_id, is_default=item.is_default)
        for item in catalog
        if item.is_available and item.is_default
    ]


@app.get("/api/v1/projects/{project_id}/jobs", response_model=list[JobOut])
async def list_jobs(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    """List recent generation jobs for one project owned by the current user."""

    stmt = select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc())
    jobs = (await db.execute(stmt)).scalars().all()
    return [_job_out(job) for job in jobs]


@app.post(
    "/api/v1/projects/{project_id}/jobs/{job_id}/cancel",
    response_model=JobOut,
)
async def cancel_job(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Request cancellation for one queued or running job owned by the user."""

    job = await _get_project_job(project, job_id, db)
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return _job_out(job)
    job.cancellation_requested = True
    if job.status is JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        db.add(JobEvent(job_id=job.id, event_type="status", payload={"status": "cancelled"}))
    else:
        db.add(JobEvent(job_id=job.id, event_type="agent", payload={"text": "正在中止 OpenCode 任务..."}))
    await db.commit()
    await db.refresh(job)
    return _job_out(job)


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/events",
    response_model=list[JobEventOut],
)
async def list_job_events(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[JobEventOut]:
    """Replay all persisted events for an authorized generation job."""

    await _get_project_job(project, job_id, db)
    stmt = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    events = (await db.execute(stmt)).scalars().all()
    return [
        JobEventOut(
            id=event.id,
            event_type=event.event_type,
            payload=event.payload,
            created_at=event.created_at,
        )
        for event in events
    ]


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/events/stream")
async def stream_job_events(
    request: Request,
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream persisted job events so reconnecting clients can resume safely."""

    await _get_project_job(project, job_id, db)
    last_event_id = int(request.headers.get("Last-Event-ID", "0") or 0)

    async def event_stream() -> AsyncIterator[str]:
        nonlocal last_event_id
        while True:
            async with SessionLocal() as stream_db:
                stmt = (
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.id > last_event_id)
                    .order_by(JobEvent.id)
                )
                events = (await stream_db.execute(stmt)).scalars().all()
                current_job = await stream_db.get(Job, job_id)
                latest_editor_export = (
                    await stream_db.execute(
                        select(JobEvent)
                        .where(
                            JobEvent.job_id == job_id,
                            JobEvent.event_type == "editor_export",
                        )
                        .order_by(JobEvent.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                editor_export_active = (
                    latest_editor_export is not None
                    and str(latest_editor_export.payload.get("status", ""))
                    in {"queued", "exporting"}
                )
            for event in events:
                last_event_id = event.id
                data = json.dumps(
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    },
                    ensure_ascii=False,
                )
                yield f"id: {event.id}\nevent: job-event\ndata: {data}\n\n"
            if current_job and current_job.status.value in {"succeeded", "failed", "cancelled"}:
                if not editor_export_active:
                    yield "event: complete\ndata: {}\n\n"
                    return
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/artifacts",
    response_model=list[ArtifactOut],
)
async def list_job_artifacts(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactOut]:
    """List artifacts produced by an authorized job."""

    await _get_project_job(project, job_id, db)
    stmt = select(Artifact).where(Artifact.job_id == job_id).order_by(Artifact.created_at)
    artifacts = (await db.execute(stmt)).scalars().all()
    return [_artifact_out(artifact) for artifact in artifacts]


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: UUID,
    artifact_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download one artifact after project and job ownership checks."""

    await _get_project_job(project, job_id, db)
    artifact = (
        await db.execute(
            select(Artifact).where(Artifact.id == artifact_id, Artifact.job_id == job_id)
        )
    ).scalar_one_or_none()
    if not artifact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    project_root = _workspace_path(project)
    artifact_path = (project_root / artifact.relative_path).resolve()
    if project_root not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return FileResponse(
        artifact_path,
        media_type=artifact.content_type,
        filename=Path(artifact.relative_path).name,
    )


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides",
    response_model=list[EditorSlideOut],
)
async def list_editor_slides(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[EditorSlideOut]:
    """List SVG authoring pages for one finished, user-owned task."""

    job = await _get_project_job(project, job_id, db)
    if job.status is not JobStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "任务完成后才可以进入手动编辑")
    slides = (
        await db.execute(
            select(Artifact)
            .where(Artifact.job_id == job.id, Artifact.kind == ArtifactKind.SVG)
            .order_by(Artifact.relative_path)
        )
    ).scalars().all()
    return [
        EditorSlideOut(
            id=slide.id,
            filename=Path(slide.relative_path).name,
            size_bytes=slide.size_bytes,
        )
        for slide in slides
    ]


async def _get_editor_slide(
    project: Project,
    job_id: UUID,
    artifact_id: UUID,
    db: AsyncSession,
) -> Artifact:
    await _get_project_job(project, job_id, db)
    artifact = (
        await db.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.job_id == job_id,
                Artifact.kind == ArtifactKind.SVG,
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SVG 页面不存在")
    return artifact


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides/{artifact_id}")
async def get_editor_slide(
    job_id: UUID,
    artifact_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve one editable SVG inline after task ownership verification."""

    artifact = await _get_editor_slide(project, job_id, artifact_id, db)
    content = _editor_artifact_path(project, artifact).read_text(encoding="utf-8")
    return Response(content, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.put("/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides/{artifact_id}")
async def save_editor_slide(
    job_id: UUID,
    artifact_id: UUID,
    payload: EditorSlideSaveIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> EditorSlideOut:
    """Persist one browser-edited SVG source page for a finished task."""

    active = (
        await db.execute(
            select(Job.id)
            .where(
                Job.project_id == project.id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "当前对话仍有任务在执行，暂不能手动保存")
    artifact = await _get_editor_slide(project, job_id, artifact_id, db)
    _validate_editor_svg(payload.content)
    artifact_path = _editor_artifact_path(project, artifact)
    artifact_path.write_text(payload.content, encoding="utf-8")
    artifact.size_bytes = artifact_path.stat().st_size
    db.add(
        JobEvent(
            job_id=job_id,
            event_type="editor_export",
            payload={
                "status": "queued",
                "text": "已保存 SVG，正在导出新版演示文稿。",
            },
        )
    )
    await db.commit()
    from runner.celery_app import celery_app

    celery_app.send_task("runner.export_editor_revision", args=[str(job_id)])
    return EditorSlideOut(
        id=artifact.id,
        filename=Path(artifact.relative_path).name,
        size_bytes=artifact.size_bytes,
    )


@app.get("/")
async def frontend() -> FileResponse:
    """Serve the built React application when present in the image."""

    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Frontend build is not available")
    return FileResponse(index)


@app.get("/{frontend_path:path}", include_in_schema=False)
async def frontend_route(frontend_path: str) -> FileResponse:
    """Serve built static files, then fall back to the SPA shell for app routes."""

    if frontend_path.startswith("api/"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    # Vite copies public files such as the application logo to the dist root.
    # They must be served as files before treating an unknown path as an SPA route.
    static_file = (FRONTEND_DIST / frontend_path).resolve()
    if FRONTEND_DIST.resolve() in static_file.parents and static_file.is_file():
        return FileResponse(static_file)
    return await frontend()
    ModelOut,
