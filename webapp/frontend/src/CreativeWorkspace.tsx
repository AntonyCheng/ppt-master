import { CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Edit3,
  AlertTriangle,
  FileText,
  LayoutTemplate,
  LoaderCircle,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Square,
  Trash2,
  Upload,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  X,
  Zap,
} from "lucide-react";

type Project = { id: string; title: string };
type ProjectMaterial = { id: string; original_filename: string; content_type: string; size_bytes: number; status: "processing" | "ready" | "failed"; error: string | null };
type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
type Job = {
  id: string;
  base_job_id: string | null;
  target_slide_number: number | null;
  template_name: string | null;
  status: JobStatus;
  prompt: string;
  error: string | null;
  cancellation_requested: boolean;
};
type Template = {
  id: string;
  name: string;
  page_count: number | null;
  metadata: Record<string, unknown>;
};
type PromptSnippet = { id: string; name: string; content: string };
type Artifact = { id: string; kind: "svg" | "pptx" | "report"; filename: string };
type JobEvent = { id: number; event_type: string; payload: Record<string, unknown>; created_at: string };
type ChatMessage = { id: string; role: "user" | "assistant"; content: string; pending?: boolean };
type RefinementMessage = { id: string; job_id: string | null; slide_number: number; role: "user" | "assistant"; content: string; message_order: number; created_at: string };
type RefinementIntent = { action: "modify_current_slide" | "ask_clarification" | "answer_only" | "unsupported"; confidence: number; normalized_request: string; reply: string; clarification_question: string };
type CreativeStage = "requirements" | "outline" | "template" | "generating" | "preview";
type OutlineSlide = { title: string; purpose: string; kind: string; notes: string };
type CreativeState = {
  stage: CreativeStage;
  requirements: Record<string, unknown>;
  outline: OutlineSlide[];
  notes_enabled: boolean;
  selected_template_id: string | null;
};
type Props = {
  project: Project;
  job: Job | null;
  workingJob: Job | null;
  templates: Template[];
  snippets: PromptSnippet[];
  artifacts: Artifact[];
  events: JobEvent[];
  materials: ProjectMaterial[];
  materialUploading: boolean;
  onUploadMaterials: (files: File[]) => void;
  onDeleteMaterial: (materialId: string) => void;
  initialTemplateId: string | null;
  baseJobId: string | null;
  onBack: () => void;
  onGenerate: (prompt: string, templateId: string | null, baseJobId?: string | null, targetSlideNumber?: number | null, conversationMessage?: string, clientMessageId?: string) => Promise<Job | null>;
  onCancel: () => void;
  onReturnToBase: (baseJobId: string) => void;
};

const stages: Array<{ id: CreativeStage; label: string; description: string }> = [
  { id: "requirements", label: "需求梳理", description: "确认表达目标" },
  { id: "outline", label: "大纲设计", description: "逐页组织内容" },
  { id: "template", label: "选择模板", description: "确定视觉表达" },
  { id: "generating", label: "生成 PPT", description: "实时生成任务" },
  { id: "preview", label: "预览精修", description: "查看与继续修改" },
];
const pageRangeOptions = ["1-4 页", "5-7 页", "8-10 页", "10-12 页", "12-15 页", "15-20 页", "20 页以上"];

function api<T>(path: string, options?: RequestInit): Promise<T> {
  return fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  }).then(async (response) => {
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(String(body.detail || "请求失败，请稍后重试。"));
    }
    return response.json() as Promise<T>;
  });
}

function artifactUrl(projectId: string, jobId: string, artifactId: string): string {
  return `/api/v1/projects/${projectId}/jobs/${jobId}/artifacts/${artifactId}/download`;
}

function pageCountForRange(value: unknown): number {
  const text = String(value || "8-10 页");
  const range = text.match(/(\d+)\s*[-至]\s*(\d+)/);
  if (range) return Math.max(1, Number(range[1]));
  const above = text.match(/(\d+)\s*页以上/);
  return above ? Math.max(1, Number(above[1])) : 8;
}

function suggestedOutline(topic: string, count = 8): OutlineSlide[] {
  const title = topic.trim() || "本次演示主题";
  const base: OutlineSlide[] = [
    { title, purpose: "建立汇报主题、对象与核心主张", kind: "封面页", notes: "开场说明本次演示要解决的问题。" },
    { title: "核心结论", purpose: "先给出观众需要记住的关键判断", kind: "结论页", notes: "用简洁语言说明结论与行动方向。" },
    { title: "背景与关键挑战", purpose: "解释为什么现在需要关注这个议题", kind: "问题分析", notes: "补充必要背景，避免陷入细节。" },
    { title: "现状与关键数据", purpose: "用事实建立对现状的共同认知", kind: "数据图表", notes: "说明数据来源与关键变化。" },
    { title: "重点分析", purpose: "用数据、案例或结构化信息支撑判断", kind: "分析页", notes: "说明证据来源与关键洞察。" },
    { title: "解决方案与价值", purpose: "说明可行路径、预期收益和差异化", kind: "方案页", notes: "把价值转化成面向受众的收益。" },
    { title: "案例与落地参考", purpose: "用案例验证方案的可行性", kind: "案例页", notes: "提炼可复用的经验与边界。" },
    { title: "实施计划", purpose: "明确责任、节奏和关键里程碑", kind: "行动计划", notes: "列出近期可执行的行动项。" },
    { title: "风险与应对", purpose: "提前识别主要风险和缓解措施", kind: "风险页", notes: "说明风险等级与预案。" },
    { title: "下一步行动", purpose: "明确责任、节奏和需要支持的事项", kind: "行动计划", notes: "以明确的行动项结束演示。" },
  ];
  while (base.length < count) {
    const number = base.length - 10;
    base.splice(base.length - 1, 0, { title: `补充分析 ${number + 1}`, purpose: "补充支撑主题判断的关键信息", kind: "内容页", notes: "围绕主题补充必要信息。" });
  }
  return base.slice(0, Math.max(1, count));
}

function initialRequirements(project: Project, source: Record<string, unknown>): Record<string, unknown> {
  return {
    topic: String(source.topic || project.title),
    scenario: String(source.scenario || "业务汇报"),
    audience: String(source.audience || "相关决策者与项目参与方"),
    page_range: String(source.page_range || "12-15 页"),
    style: String(source.style || "专业、简洁、结论先行"),
    objective: String(source.objective || "讲清楚背景、判断、解决方案与下一步行动。"),
  };
}

function stageIndex(stage: CreativeStage): number {
  return stages.findIndex((item) => item.id === stage);
}

export function CreativeWorkspace(props: Props) {
  const [state, setState] = useState<CreativeState | null>(null);
  const [requirements, setRequirements] = useState<Record<string, unknown>>({});
  const [outline, setOutline] = useState<OutlineSlide[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState(0);
  const [refinement, setRefinement] = useState("");
  const [notice, setNotice] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isGeneratingOutline, setIsGeneratingOutline] = useState(false);
  const [showRegenerateConfirm, setShowRegenerateConfirm] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [pendingChatJobId, setPendingChatJobId] = useState<string | null>(null);
  const [isClassifyingIntent, setIsClassifyingIntent] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatSlideRef = useRef<number | null>(null);

  const currentJob = props.workingJob || props.job;
  const pageRefinementConversation = Boolean(currentJob?.target_slide_number);
  const effectiveStage: CreativeStage = props.workingJob
    ? "generating"
    : props.job?.status === "succeeded"
      ? "preview"
      : props.job?.status === "failed" || props.job?.status === "cancelled"
        ? "generating"
      : state?.stage || "requirements";
  const currentIndex = stageIndex(effectiveStage);
  const previewSlides = useMemo(
    () => props.artifacts.filter((artifact) => artifact.kind === "svg").sort((left, right) => left.filename.localeCompare(right.filename, undefined, { numeric: true })),
    [props.artifacts],
  );
  const latestPptx = useMemo(
    () => props.artifacts.filter((artifact) => artifact.kind === "pptx").at(-1) || null,
    [props.artifacts],
  );
  const selectedTemplate = props.templates.find((template) => template.id === selectedTemplateId) || null;
  const progress = useMemo(() => {
    if (props.job?.status === "succeeded") return 100;
    if (props.job?.status === "failed") {
      if (props.events.some((event) => event.event_type === "validation")) return 92;
      if (props.events.some((event) => event.event_type === "artifact")) return 84;
      if (props.events.some((event) => event.event_type === "tool" || event.event_type === "agent" || event.event_type === "opencode")) return 58;
      return 36;
    }
    if (!props.workingJob) return 0;
    if (props.events.some((event) => event.event_type === "artifact")) return 84;
    if (props.events.some((event) => event.event_type === "opencode")) return 58;
    if (props.events.some((event) => event.payload.status === "running")) return 36;
    return 12;
  }, [props.events, props.job?.status, props.workingJob]);
  const progressMessage = useMemo(() => {
    const latest = props.events.at(-1)?.payload;
    return String(latest?.message || latest?.text || latest?.status || "正在准备生成环境");
  }, [props.events]);

  useEffect(() => {
    let ignored = false;
    void api<CreativeState>(`/api/v1/projects/${props.project.id}/creative-state`).then((nextState) => {
      if (ignored) return;
      setState(nextState);
      setRequirements(initialRequirements(props.project, nextState.requirements));
      setOutline(nextState.outline.length ? nextState.outline : suggestedOutline(props.project.title, pageCountForRange(nextState.requirements.page_range)));
      setSelectedTemplateId(nextState.selected_template_id || props.initialTemplateId);
    }).catch((error: unknown) => {
      if (!ignored) setNotice(error instanceof Error ? error.message : "无法载入创作工作台。");
    });
    return () => { ignored = true; };
  }, [props.initialTemplateId, props.project.id, props.project.title]);

  useEffect(() => {
    if (activeSlide >= previewSlides.length) setActiveSlide(Math.max(0, previewSlides.length - 1));
  }, [activeSlide, previewSlides.length]);

  useEffect(() => {
    setChatMessages([]);
    setPendingChatJobId(null);
    chatSlideRef.current = null;
  }, [props.project.id]);

  useEffect(() => {
    if (effectiveStage !== "preview" && !pageRefinementConversation) return;
    if (chatSlideRef.current !== null && chatSlideRef.current !== activeSlide) {
      setChatMessages([]);
      setPendingChatJobId(null);
    }
    chatSlideRef.current = activeSlide;
    let ignored = false;
    void api<RefinementMessage[]>(`/api/v1/projects/${props.project.id}/refinement-messages?slide_number=${activeSlide + 1}`)
      .then((messages) => {
        if (ignored) return;
        const restored: ChatMessage[] = [...new Map(messages.sort((left, right) => left.message_order - right.message_order).map((message) => [message.id, message])).values()].map((message) => ({
          id: message.id,
          role: message.role,
          content: message.content,
        }));
        if (currentJob?.status === "queued" || currentJob?.status === "running") {
          restored.push({
            id: `pending-${currentJob.id}`,
            role: "assistant",
            content: "已收到，我正在根据你的要求修改当前页面。",
            pending: true,
          });
        }
        setChatMessages(restored.length ? restored : [{
          id: `welcome-${props.project.id}-${activeSlide}`,
          role: "assistant",
          content: "你好，我可以基于当前选中的页面继续修改。你可以直接告诉我想改什么，其他页面会保持不变。",
        }]);
      })
      .catch(() => undefined);
    return () => { ignored = true; };
  }, [activeSlide, currentJob?.id, effectiveStage, pageRefinementConversation, props.project.id]);

  useEffect(() => {
    const trackedChatJobId = pendingChatJobId || (props.job?.target_slide_number ? props.job.id : null);
    if (!trackedChatJobId || !props.job || props.job.id !== trackedChatJobId) return;
    if (props.job.status === "queued" || props.job.status === "running") {
      setChatMessages((current) => current.map((message) => message.pending && (pendingChatJobId || message.id === `pending-${trackedChatJobId}`) ? { ...message, content: "已收到，我正在根据你的要求修改当前页面。" } : message));
      return;
    }
    const content = props.job.status === "succeeded"
      ? "当前页面已修改完成，结果已应用到当前 PPT。你还可以继续告诉我需要调整的地方。"
      : props.job.status === "cancelled"
        ? "这次修改已中止，当前 PPT 没有变化。你可以继续发送新的修改要求。"
        : "这次修改没有完成，当前 PPT 没有变化。你可以直接重试，或换一种方式描述修改要求。";
    setChatMessages((current) => current.map((message) => message.pending && (pendingChatJobId || message.id === `pending-${trackedChatJobId}`) ? { ...message, content, pending: false } : message));
    setPendingChatJobId(null);
  }, [pendingChatJobId, props.job?.id, props.job?.status]);

  useEffect(() => {
    const node = chatScrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [chatMessages]);

  async function saveWorkspace(next: Partial<CreativeState>): Promise<CreativeState | null> {
    setIsSaving(true);
    setNotice("");
    try {
      const saved = await api<CreativeState>(`/api/v1/projects/${props.project.id}/creative-state`, {
        method: "PUT",
        body: JSON.stringify(next),
      });
      setState(saved);
      return saved;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存创作内容失败。");
      return null;
    } finally {
      setIsSaving(false);
    }
  }

  async function generateOutline(): Promise<void> {
    const topic = String(requirements.topic || "").trim();
    if (!topic) {
      setNotice("请先填写 PPT 主题。");
      return;
    }
    setIsGeneratingOutline(true);
    setNotice("");
    try {
      const generated = await api<CreativeState>(`/api/v1/projects/${props.project.id}/creative-outline`, {
        method: "POST",
        body: JSON.stringify({ requirements }),
      });
      setState(generated);
      setRequirements(initialRequirements(props.project, generated.requirements));
      setOutline(generated.outline.length ? generated.outline : suggestedOutline(topic, pageCountForRange(requirements.page_range)));
      setNotice("");
    } catch (error) {
      const count = pageCountForRange(requirements.page_range);
      const fallback = suggestedOutline(topic, count);
      setOutline(fallback);
      const fallbackState = await saveWorkspace({ stage: "outline", requirements, outline: fallback });
      if (fallbackState) setNotice("模型暂时不可用，已生成本地大纲草稿，仍可继续编辑。");
      else setNotice(error instanceof Error ? error.message : "大纲生成失败，已保留当前需求。");
    } finally {
      setIsGeneratingOutline(false);
    }
  }

  async function confirmRequirements(): Promise<void> {
    const topic = String(requirements.topic || "").trim();
    if (!topic) {
      setNotice("请先填写 PPT 主题。");
      return;
    }
    const hasSavedOutline = Boolean(state?.outline?.length);
    const saved = await saveWorkspace({
      stage: hasSavedOutline ? "outline" : undefined,
      requirements,
      notes_enabled: state?.notes_enabled ?? true,
    });
    if (!saved) return;
    if (hasSavedOutline) {
      setOutline(saved.outline);
      setNotice("");
      return;
    }
    await generateOutline();
  }

  async function requestRegenerateOutline(): Promise<void> {
    setShowRegenerateConfirm(false);
    const saved = await saveWorkspace({ requirements, notes_enabled: state?.notes_enabled ?? true });
    if (!saved) return;
    await generateOutline();
  }

  async function confirmOutline(): Promise<void> {
    const valid = outline.filter((slide) => slide.title.trim());
    if (!valid.length) {
      setNotice("大纲至少需要保留一页内容。");
      return;
    }
    const saved = await saveWorkspace({ stage: "template", outline: valid });
    if (saved) setOutline(valid);
  }

  async function confirmTemplate(): Promise<void> {
    const saved = await saveWorkspace({ stage: "generating", selected_template_id: selectedTemplateId });
    if (!saved) return;
    const prompt = String(requirements.topic || props.project.title).trim();
    await props.onGenerate(prompt, selectedTemplateId);
  }

  async function submitRefinement(): Promise<void> {
    const requestText = refinement.trim();
    if (!requestText) return;
    if (!props.job) return;
    const pageTitle = activeOutline?.title || activePreview?.filename || `第 ${activeSlide + 1} 页`;
    const clientMessageId = crypto.randomUUID();
    const userMessage: ChatMessage = { id: `user-${clientMessageId}`, role: "user", content: requestText };
    const assistantMessage: ChatMessage = { id: `assistant-${Date.now()}`, role: "assistant", content: "正在理解你的要求…", pending: true };
    setChatMessages((current) => [...current, userMessage, assistantMessage]);
    setRefinement("");
    setIsClassifyingIntent(true);
    try {
      const intent = await api<RefinementIntent>(`/api/v1/projects/${props.project.id}/refinement-intent`, {
        method: "POST",
        body: JSON.stringify({ slide_number: activeSlide + 1, slide_title: pageTitle, message: requestText, client_message_id: clientMessageId }),
      });
      if (intent.action !== "modify_current_slide") {
        const response = intent.action === "ask_clarification"
          ? intent.clarification_question
          : intent.reply;
        setChatMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: response || "请说明你想如何修改当前页面。", pending: false } : message));
        return;
      }
      if (!activePreview) {
        setChatMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: "当前页面文件尚未准备好，暂时不能提交修改。请先完成或重试 PPT 生成。", pending: false } : message));
        return;
      }
      setChatMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: "已理解，我正在提交当前页面修改。" } : message));
      const scopedPrompt = [
        `仅修改当前选中的第 ${activeSlide + 1} 页（${pageTitle}）。`,
        `目标页面文件：${activePreview.filename}。`,
        "保留其他页面、页面顺序、模板和整体风格不变。",
        "完成后重新导出完整 PPTX，并确保目标页面仍然可编辑。",
        `用户修改要求：${intent.normalized_request || requestText}`,
      ].join("\n");
      const created = await props.onGenerate(scopedPrompt, null, props.job.id, activeSlide + 1, requestText, clientMessageId);
      if (created) {
        setPendingChatJobId(created.id);
      } else {
        setChatMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: "修改任务未能提交，当前 PPT 没有变化，请稍后重试。", pending: false } : message));
      }
    } catch (error) {
      setChatMessages((current) => current.map((message) => message.id === assistantMessage.id ? { ...message, content: error instanceof Error ? error.message : "暂时无法理解这条消息，请稍后重试。", pending: false } : message));
    } finally {
      setIsClassifyingIntent(false);
    }
  }

  async function retryGeneration(continueFromFailure: boolean): Promise<void> {
    if (!props.job || isRetrying || props.workingJob) return;
    setIsRetrying(true);
    setNotice("");
    try {
      const prompt = continueFromFailure ? props.job.prompt : String(requirements.topic || props.project.title).trim();
      const retryBaseJobId = props.job.target_slide_number
        ? (props.job.base_job_id || props.baseJobId || null)
        : props.job.id;
      await props.onGenerate(prompt, null, continueFromFailure ? retryBaseJobId : null, continueFromFailure ? props.job.target_slide_number : null);
    } finally {
      setIsRetrying(false);
    }
  }

  function returnToBasePpt(): void {
    const baseJobId = currentJob?.base_job_id || props.baseJobId;
    if (baseJobId) props.onReturnToBase(baseJobId);
    else setNotice("原演示文稿暂时不可用，请刷新项目后重试。");
  }

  function editSlide(index: number, key: keyof OutlineSlide, value: string): void {
    setOutline((current) => current.map((slide, slideIndex) => slideIndex === index ? { ...slide, [key]: value } : slide));
  }

  function moveSlide(index: number, direction: -1 | 1): void {
    const target = index + direction;
    if (target < 0 || target >= outline.length) return;
    setOutline((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function changeStage(next: CreativeStage): void {
    const canReturnToOutline = next === "outline" && outline.length > 0;
    if (props.workingJob || isGeneratingOutline || next === "generating" || next === "preview" || (stageIndex(next) > currentIndex && !canReturnToOutline)) return;
    void saveWorkspace({ stage: next });
  }

  const activePreview = previewSlides[activeSlide] || null;
  const activeOutline = outline[activeSlide] || null;
  const chatIsPageScoped = effectiveStage === "preview" || pageRefinementConversation;

  return (
    <main className="zc-creative-workspace kppt-workbench-shell">
      <header className="zc-workbench-head zc-workbench-head--rich">
        <button className="zc-icon" type="button" aria-label="返回我的 PPT" onClick={props.onBack}><ArrowLeft size={19} /></button>
        <div><strong>{props.project.title}</strong><span>{selectedTemplate ? `使用模板：${selectedTemplate.name}` : currentJob?.template_name ? `使用模板：${currentJob.template_name}` : "自由创作"}</span></div>
        <div className="zc-workbench-head-actions"><span className={`zc-status zc-status-${currentJob?.status || "draft"}`}>{props.workingJob ? (currentJob?.target_slide_number ? `正在修改第 ${currentJob.target_slide_number} 页` : "生成中") : props.job?.status === "succeeded" ? "已完成" : "创作草稿"}</span>{effectiveStage === "preview" && currentJob && <a className="zc-secondary zc-workbench-manual-edit" href={`/editor/${props.project.id}/${currentJob.id}`}><Pencil size={14} />手动编辑</a>}{effectiveStage === "preview" && latestPptx && currentJob && <a className="zc-primary zc-workbench-export" href={artifactUrl(props.project.id, currentJob.id, latestPptx.id)} download><Download size={14} />导出 PPTX</a>}</div>
      </header>
      <div className="zc-stage-bar zc-stage-bar--rich">
        {stages.map((item, index) => <button key={item.id} type="button" className={`${index <= currentIndex ? "is-active" : ""} ${item.id === effectiveStage ? "is-current" : ""}`} onClick={() => changeStage(item.id)} disabled={(index > currentIndex && !(item.id === "outline" && outline.length > 0)) || Boolean(props.workingJob) || isGeneratingOutline}><i>{index < currentIndex ? <Check size={13} /> : index + 1}</i><span>{item.label}</span></button>)}
      </div>
      <div className="zc-creative-layout">
        <aside className={`zc-workbench-rail ${effectiveStage === "preview" ? "zc-workbench-rail--preview" : ""}`}>{effectiveStage === "preview" ? <div className="zc-rail-slides">{previewSlides.map((slide, index) => <button className={index === activeSlide ? "is-active" : ""} type="button" key={slide.id} onClick={() => setActiveSlide(index)}><img src={currentJob ? artifactUrl(props.project.id, currentJob.id, slide.id) : ""} alt={`第 ${index + 1} 页缩略图`} /><span>{index + 1}</span><small>{outline[index]?.title || slide.filename}</small></button>)}</div> : <><div className="zc-rail-heading"><span>创作流程</span><strong>{`${Math.min(currentIndex + 1, stages.length)} / ${stages.length}`}</strong></div><div className="zc-stage-navigation">{stages.map((item, index) => <button type="button" key={item.id} className={item.id === effectiveStage ? "is-active" : ""} onClick={() => changeStage(item.id)} disabled={(index > currentIndex && !(item.id === "outline" && outline.length > 0)) || Boolean(props.workingJob) || isGeneratingOutline}><i>{index < currentIndex ? <Check size={12} /> : index + 1}</i><span><strong>{item.label}</strong><small>{item.description}</small></span></button>)}</div></>}</aside>
        <section className={`zc-workbench-main zc-workbench-main--${effectiveStage}`}>
          {!state ? <div className="zc-workbench-loading"><LoaderCircle className="zc-spin" size={28} />正在载入创作工作台</div> : effectiveStage === "requirements" && isGeneratingOutline ? <OutlineSkeletonPanel /> : effectiveStage === "requirements" ? <RequirementsPanel requirements={requirements} setRequirements={setRequirements} notesEnabled={state.notes_enabled} setNotesEnabled={(value) => setState({ ...state, notes_enabled: value })} materials={props.materials} materialUploading={props.materialUploading} onUploadMaterials={props.onUploadMaterials} onDeleteMaterial={props.onDeleteMaterial} onConfirm={() => void confirmRequirements()} busy={isSaving || isGeneratingOutline} hasOutline={Boolean(state.outline.length)} /> : effectiveStage === "outline" ? <OutlinePanel outline={outline} onEdit={editSlide} onMove={moveSlide} onAdd={() => setOutline((current) => [...current, { title: "新页面", purpose: "说明本页希望观众理解什么", kind: "内容页", notes: "补充本页讲解词。" }])} onRemove={(index) => setOutline((current) => current.filter((_, slideIndex) => slideIndex !== index))} onConfirm={() => void confirmOutline()} onRegenerate={() => setShowRegenerateConfirm(true)} busy={isSaving || isGeneratingOutline} /> : effectiveStage === "template" ? <TemplatePanel templates={props.templates} selectedTemplateId={selectedTemplateId} onSelect={setSelectedTemplateId} onConfirm={() => void confirmTemplate()} busy={isSaving} /> : effectiveStage === "generating" ? <GeneratingPanel progress={progress} message={progressMessage} artifactCount={previewSlides.length} targetSlideNumber={currentJob?.target_slide_number ?? null} status={currentJob?.status || "queued"} error={currentJob?.error || null} events={props.events} canCancel={Boolean(props.workingJob)} canReturnToBase={Boolean(currentJob?.target_slide_number && (currentJob?.base_job_id || props.baseJobId))} retrying={isRetrying} onRetry={(continueFromFailure) => void retryGeneration(continueFromFailure)} cancelling={Boolean(props.workingJob?.cancellation_requested)} onCancel={props.onCancel} onReturnToBase={returnToBasePpt} /> : <PreviewPanel project={props.project} job={props.job} activeArtifact={activePreview} activeOutline={activeOutline} />}
          {notice && !isGeneratingOutline && <p className="zc-inline-notice">{notice}</p>}
        </section>
        <aside className={`zc-workbench-chat ${chatIsPageScoped ? "zc-workbench-chat--preview" : ""}`}><header><span><Sparkles size={16} /></span><div><strong>{chatIsPageScoped ? "AI 页面助手" : "AI 创作助手"}</strong><small><i /> {chatIsPageScoped ? (props.workingJob ? "当前页修改进行中" : isClassifyingIntent ? "正在理解你的要求" : "当前页可交互修改") : "当前可继续补充需求"}</small></div></header>{chatIsPageScoped ? <div className="zc-chat-editor"><div className="zc-chat-context"><span>当前对话范围</span><strong>第 {activeSlide + 1} 页 · {activeOutline?.title || activePreview?.filename || "当前页面"}</strong><small>只会修改当前页，其他页面保持不变。</small></div><div className="zc-chat-thread" ref={chatScrollRef} aria-live="polite">{chatMessages.map((message) => <article className={`zc-chat-message zc-chat-message--${message.role}`} key={message.id}><span className="zc-chat-avatar">{message.role === "assistant" ? <Sparkles size={13} /> : "你"}</span><div><strong>{message.role === "assistant" ? "AI 页面助手" : "你"}</strong><p>{message.content}{message.pending && <LoaderCircle size={13} className="zc-spin zc-chat-pending" />}</p></div></article>)}</div><label className="zc-chat-composer"><span className="sr-only">发送消息</span><textarea rows={3} value={refinement} onChange={(event) => setRefinement(event.target.value)} onKeyDown={(event) => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); void submitRefinement(); } }} placeholder="告诉我想如何修改这一页…" disabled={Boolean(props.workingJob) || Boolean(pendingChatJobId) || isClassifyingIntent} /><button className="zc-chat-send" type="button" aria-label="发送消息" title="发送消息" onClick={() => void submitRefinement()} disabled={!refinement.trim() || Boolean(props.workingJob) || Boolean(pendingChatJobId) || isClassifyingIntent}><Send size={16} /></button></label></div> : <><div className="zc-chat-context"><span>当前阶段</span><strong>{stages[currentIndex].label}</strong><small>确认内容后会自动保存</small></div><div className="zc-chat-note"><MessageSquareText size={16} /><span>常用提示词可作为补充要求，在预览精修阶段提交。</span></div></>}</aside>
      </div>
      {showRegenerateConfirm && <div className="zc-dialog-backdrop" role="presentation"><section className="zc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="regenerate-outline-title"><header><div><strong id="regenerate-outline-title">重新生成大纲？</strong><span>新的大纲会覆盖当前已保存的大纲内容。</span></div><button className="zc-icon" type="button" aria-label="关闭重新生成确认" onClick={() => setShowRegenerateConfirm(false)}><X size={17} /></button></header><div className="zc-confirm-dialog-body"><p>系统会根据当前需求、页数范围和已添加材料重新规划页面。当前编辑内容不会自动合并到新大纲。</p><div><button className="zc-secondary" type="button" onClick={() => setShowRegenerateConfirm(false)}>取消</button><button className="zc-primary" type="button" onClick={() => void requestRegenerateOutline()}><RefreshCw size={15} />重新生成</button></div></div></section></div>}
    </main>
  );
}

function RequirementsPanel(props: { requirements: Record<string, unknown>; setRequirements: (value: Record<string, unknown>) => void; notesEnabled: boolean; setNotesEnabled: (value: boolean) => void; materials: ProjectMaterial[]; materialUploading: boolean; onUploadMaterials: (files: File[]) => void; onDeleteMaterial: (materialId: string) => void; onConfirm: () => void; busy: boolean; hasOutline: boolean }) {
  const update = (key: string, value: string) => props.setRequirements({ ...props.requirements, [key]: value });
  return <div className="zc-stage-page"><header className="zc-stage-page-head"><div><span>01 · 需求梳理</span><h1>确认这次要讲清楚什么</h1><p>结构化需求会作为后续大纲和生成任务的共同上下文。</p></div><small>项目自动保存</small></header><section className="zc-requirement-card"><header><div><FileText size={18} /><strong>结构化 PPT 需求单</strong></div><span>可编辑</span></header><div className="zc-requirement-grid"><label className="is-wide"><span>PPT 主题</span><input value={String(props.requirements.topic || "")} onChange={(event) => update("topic", event.target.value)} /></label><label><span>使用场景</span><input value={String(props.requirements.scenario || "")} onChange={(event) => update("scenario", event.target.value)} /></label><label><span>目标受众</span><input value={String(props.requirements.audience || "")} onChange={(event) => update("audience", event.target.value)} /></label><label><span>页数范围</span><select value={String(props.requirements.page_range || pageRangeOptions[2])} onChange={(event) => update("page_range", event.target.value)}>{pageRangeOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label><label><span>整体风格</span><input value={String(props.requirements.style || "")} onChange={(event) => update("style", event.target.value)} /></label><label className="is-wide"><span>核心目标</span><textarea rows={4} value={String(props.requirements.objective || "")} onChange={(event) => update("objective", event.target.value)} /></label></div><footer><div><strong>生成每页讲解词</strong><span>在大纲中保存讲解重点，并在生成时提供给模型。</span></div><button className={`zc-switch ${props.notesEnabled ? "is-on" : ""}`} type="button" aria-pressed={props.notesEnabled} onClick={() => props.setNotesEnabled(!props.notesEnabled)}><i /></button></footer></section><section className="zc-material-card"><header><div><Upload size={17} /><strong>创作材料</strong><span>{props.materials.length ? `已添加 ${props.materials.length} 份` : "可选"}</span></div><label className="zc-secondary zc-material-upload">{props.materialUploading ? "正在上传…" : "添加材料"}<input type="file" multiple accept=".pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp" onChange={(event) => { props.onUploadMaterials(Array.from(event.target.files || [])); event.target.value = ""; }} disabled={props.materialUploading} /></label></header>{props.materials.length ? <div className="zc-material-list">{props.materials.map((material) => <div key={material.id}><span>{material.original_filename}</span><small>{material.status === "ready" ? "已保存，生成时解析" : material.status === "processing" ? "处理中" : material.error || "处理失败"}</small><button type="button" aria-label={`删除 ${material.original_filename}`} onClick={() => props.onDeleteMaterial(material.id)}><Trash2 size={14} /></button></div>)}</div> : <p className="zc-material-empty">添加 PDF、Word、PPT、表格或文本，OpenCode 会在生成前读取它们。</p>}</section><div className="zc-stage-actions"><span><Sparkles size={15} />{props.hasOutline ? "需求已保存，可继续查看现有大纲" : "需求确认后将生成一份可编辑的大纲草稿"}</span><button className="zc-primary zc-primary-large" type="button" onClick={props.onConfirm} disabled={props.busy}>{props.hasOutline ? "下一步：查看大纲" : "下一步：设计大纲"}<ChevronRight size={16} /></button></div></div>;
}

function OutlinePanel(props: { outline: OutlineSlide[]; onEdit: (index: number, key: keyof OutlineSlide, value: string) => void; onMove: (index: number, direction: -1 | 1) => void; onAdd: () => void; onRemove: (index: number) => void; onConfirm: () => void; onRegenerate: () => void; busy: boolean }) {
  return <div className="zc-stage-page"><header className="zc-stage-page-head"><div><span>02 · 大纲设计</span><h1>逐页确认内容与表达方式</h1><p>每页都可调整标题、目标、表现形式与讲解重点，确认后生成任务会遵循此顺序。</p></div><div className="zc-stage-page-head-actions"><button className="zc-secondary" type="button" onClick={props.onRegenerate} disabled={props.busy}><RefreshCw size={14} />重新生成大纲</button><button className="zc-secondary" type="button" onClick={props.onAdd} disabled={props.busy}><Plus size={15} />新增页面</button></div></header><div className="zc-outline-list">{props.outline.map((slide, index) => <article key={`${slide.title}-${index}`}><div className="zc-outline-number">{String(index + 1).padStart(2, "0")}</div><div className="zc-outline-fields"><label><span>页面标题</span><input value={slide.title} onChange={(event) => props.onEdit(index, "title", event.target.value)} /></label><label><span>建议表现形式</span><input value={slide.kind} onChange={(event) => props.onEdit(index, "kind", event.target.value)} /></label><label className="is-wide"><span>本页目标</span><textarea rows={2} value={slide.purpose} onChange={(event) => props.onEdit(index, "purpose", event.target.value)} /></label><label className="is-wide"><span>讲解重点</span><textarea rows={2} value={slide.notes} onChange={(event) => props.onEdit(index, "notes", event.target.value)} /></label></div><div className="zc-outline-actions"><button type="button" aria-label="上移页面" onClick={() => props.onMove(index, -1)} disabled={index === 0 || props.busy}>↑</button><button type="button" aria-label="下移页面" onClick={() => props.onMove(index, 1)} disabled={index === props.outline.length - 1 || props.busy}>↓</button><button type="button" aria-label="删除页面" onClick={() => props.onRemove(index)} disabled={props.busy}><X size={15} /></button></div></article>)}</div><div className="zc-stage-actions"><span><Check size={15} />共 {props.outline.length} 页，确认后会保存当前大纲</span><button className="zc-primary zc-primary-large" type="button" onClick={props.onConfirm} disabled={props.busy}>确认大纲并选择模板<ChevronRight size={16} /></button></div></div>;
}

function OutlineSkeletonPanel() {
  return <div className="zc-stage-page zc-outline-skeleton-panel" aria-busy="true" aria-label="正在生成大纲"><header className="zc-stage-page-head"><div><span>02 · 大纲设计</span><h1>正在生成大纲</h1><p>正在根据当前需求、页数范围和创作材料组织页面结构。</p></div><span className="zc-skeleton-status"><LoaderCircle size={14} className="zc-spin" />处理中</span></header><div className="zc-outline-skeleton-list">{[0, 1, 2, 3].map((item) => <article key={item}><div className="zc-skeleton-block zc-skeleton-number" /><div><div className="zc-skeleton-block zc-skeleton-title" /><div className="zc-skeleton-block zc-skeleton-line" /><div className="zc-skeleton-block zc-skeleton-line zc-skeleton-line-short" /><div className="zc-skeleton-block zc-skeleton-notes" /></div></article>)}</div><div className="zc-skeleton-footer"><span className="zc-skeleton-block zc-skeleton-footer-line" /><span className="zc-skeleton-block zc-skeleton-footer-button" /></div></div>;
}

function TemplatePanel(props: { templates: Template[]; selectedTemplateId: string | null; onSelect: (id: string | null) => void; onConfirm: () => void; busy: boolean }) {
  return <div className="zc-stage-page"><header className="zc-stage-page-head"><div><span>03 · 选择模板</span><h1>为这套内容选择视觉风格</h1><p>可以使用已准备好的个人或系统模板，也可以保持自由创作。</p></div></header><div className="zc-template-recommend"><Sparkles size={18} /><div><strong>模板将在首次生成时固定</strong><span>后续精修基于当前演示稿，不会替换已选模板。</span></div></div><div className="zc-workspace-template-grid"><button type="button" className={!props.selectedTemplateId ? "is-selected" : ""} onClick={() => props.onSelect(null)}><span><LayoutTemplate size={23} /></span><strong>自由创作</strong><small>不使用模板工作区</small>{!props.selectedTemplateId && <i><Check size={14} /></i>}</button>{props.templates.map((template) => { const preview = Array.isArray(template.metadata.preview_files) ? template.metadata.preview_files[0] : null; return <button type="button" key={template.id} className={props.selectedTemplateId === template.id ? "is-selected" : ""} onClick={() => props.onSelect(template.id)}>{preview ? <img src={`/api/v1/templates/${template.id}/files/${String(preview)}`} alt="" /> : <span><LayoutTemplate size={23} /></span>}<strong>{template.name}</strong><small>{template.page_count || 0} 页模板</small>{props.selectedTemplateId === template.id && <i><Check size={14} /></i>}</button>; })}</div><div className="zc-stage-actions"><span><Check size={15} />{props.selectedTemplateId ? "已选择模板，生成时将锁定使用。" : "将以自由创作方式生成。"}</span><button className="zc-primary zc-primary-large" type="button" onClick={props.onConfirm} disabled={props.busy}> <Sparkles size={16} />开始生成 PPT</button></div></div>;
}

function eventLabel(event: JobEvent): string {
  const payload = event.payload;
  if (event.event_type === "usage") return usageEventLabel(payload);
  const message = payload.message || payload.text || payload.detail || payload.event || payload.status;
  if (message) return String(message);
  return event.event_type === "opencode" ? "OpenCode 已发送执行事件" : `收到 ${event.event_type} 事件`;
}

function usageNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function usageValue(tokens: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = usageNumber(tokens[key]);
    if (value !== null) return value;
  }
  return null;
}

function usageEventLabel(payload: Record<string, unknown>): string {
  const rawTokens = payload.tokens;
  const tokens = rawTokens && typeof rawTokens === "object" && !Array.isArray(rawTokens)
    ? rawTokens as Record<string, unknown>
    : {};
  const cache = tokens.cache && typeof tokens.cache === "object" && !Array.isArray(tokens.cache)
    ? tokens.cache as Record<string, unknown>
    : {};
  const input = usageValue(tokens, ["input", "input_tokens", "prompt", "prompt_tokens"]);
  const output = usageValue(tokens, ["output", "output_tokens", "completion", "completion_tokens"]);
  const reasoning = usageValue(tokens, ["reasoning", "reasoning_tokens"]);
  const cacheRead = usageValue(tokens, ["cache_read", "cacheRead", "cache_read_tokens"]) ?? usageValue(cache, ["read", "cache_read", "cacheRead"]);
  const cacheWrite = usageValue(tokens, ["cache_write", "cacheWrite", "cache_write_tokens"]) ?? usageValue(cache, ["write", "cache_write", "cacheWrite"]);
  const total = usageValue(tokens, ["total", "total_tokens"]) ?? usageNumber(payload.total_tokens) ?? usageNumber(rawTokens)
    ?? [input, output, reasoning, cacheRead, cacheWrite].reduce<number | null>((sum, value) => value === null ? sum : (sum ?? 0) + value, null);
  const cost = usageNumber(payload.cost);
  const details: string[] = [];
  if (total !== null) details.push(`总计 ${total.toLocaleString("zh-CN")} tokens`);
  if (input !== null) details.push(`输入 ${input.toLocaleString("zh-CN")}`);
  if (output !== null) details.push(`输出 ${output.toLocaleString("zh-CN")}`);
  if (reasoning !== null) details.push(`推理 ${reasoning.toLocaleString("zh-CN")}`);
  if (cacheRead !== null) details.push(`缓存读取 ${cacheRead.toLocaleString("zh-CN")}`);
  if (cacheWrite !== null) details.push(`缓存写入 ${cacheWrite.toLocaleString("zh-CN")}`);
  if (cost !== null) details.push(`费用 ${cost}`);
  return details.length ? details.join(" · ") : "用量信息暂不可用";
}

function eventKindLabel(eventType: string): string {
  return ({ agent: "Agent", opencode: "OpenCode", tool: "工具", validation: "校验", usage: "用量", artifact: "产物", status: "状态", error: "错误", permission: "权限" } as Record<string, string>)[eventType] || eventType;
}

function GeneratingPanel(props: { progress: number; message: string; artifactCount: number; targetSlideNumber: number | null; status: JobStatus; error: string | null; events: JobEvent[]; canCancel: boolean; canReturnToBase: boolean; retrying: boolean; onRetry: (continueFromFailure: boolean) => void; cancelling: boolean; onCancel: () => void; onReturnToBase: () => void }) {
  const steps = ["准备生成任务", "建立视觉规范", "逐页生成内容", "质量检查与导出"];
  const activeStep = props.progress < 36 ? 0 : props.progress < 58 ? 1 : props.progress < 84 ? 2 : 3;
  const [showLogs, setShowLogs] = useState(true);
  const failed = props.status === "failed";
  const cancelled = props.status === "cancelled";
  const refining = props.targetSlideNumber !== null;
  return <div className={`zc-generating-panel ${failed ? "is-failed" : ""}`}>
    <div className="zc-generation-ring" style={{ "--progress": `${props.progress * 3.6}deg` } as CSSProperties}><div><strong>{props.progress}%</strong><span>任务进度</span></div></div>
    <h1>{failed ? (refining ? "当前页修改失败" : "演示文稿生成失败") : cancelled ? "任务已中止" : refining ? `正在修改第 ${props.targetSlideNumber} 页` : "正在把大纲变成完整演示文稿"}</h1>
    <p>{failed ? (refining ? "当前 PPT 未更新，原内容已保留；可重试当前页修改。" : "工作流日志和失败原因已保留，可据此重新生成。") : cancelled ? "任务已停止，当前 PPT 未更新。" : refining ? "正在基于当前 PPT 修改目标页面，其他页面保持不变。" : "可离开当前页面，任务会持续在后台运行并自动保存。"}</p>
    {props.error && <div className="zc-generation-error"><AlertTriangle size={16} /><span>{props.error}</span></div>}
    {failed && !refining && props.artifactCount > 0 && <div className="zc-generation-retained">已保留 {props.artifactCount} 页已生成内容；本次任务状态异常，不会删除已有产物。</div>}
    <div className="zc-generation-progress"><header><span>任务进度</span><strong>{props.progress}%</strong></header><div><i style={{ width: `${props.progress}%` }} /></div><small>{props.artifactCount ? `已发现 ${props.artifactCount} 页 · ` : ""}{props.message}</small></div>
    <div className="zc-generation-steps-rich">{steps.map((step, index) => <div className={index < activeStep ? "is-done" : index === activeStep ? "is-active" : ""} key={step}><span>{index < activeStep ? <Check size={13} /> : index + 1}</span><strong>{step}</strong></div>)}</div>
    {props.canCancel && <button className="zc-danger" type="button" onClick={props.onCancel} disabled={props.cancelling}><Square size={15} />{props.cancelling ? "正在取消修改" : refining ? "取消修改" : "中止任务"}</button>}
    {(failed || cancelled) && <div className="zc-generation-actions">{failed && <button className="zc-secondary" type="button" disabled={props.retrying} onClick={() => props.onRetry(refining)}><RefreshCw size={14} />{props.retrying ? "正在准备…" : refining ? "重试当前页" : "重新生成"}</button>}{!refining && failed && props.artifactCount > 0 && <button className="zc-primary" type="button" disabled={props.retrying} onClick={() => props.onRetry(true)}><Sparkles size={14} />接着生成</button>}{refining && props.canReturnToBase && <button className="zc-primary" type="button" disabled={props.retrying} onClick={props.onReturnToBase}><ArrowLeft size={14} />取消修改，返回现有 PPT</button>}</div>}
    <section className="zc-opencode-log"><header><div><strong>工作流日志</strong><small>{props.events.length ? `${props.events.length} 条记录` : "暂时没有收到执行记录"}</small></div><button className="zc-secondary" type="button" onClick={() => setShowLogs((current) => !current)}><ChevronDown size={14} className={showLogs ? "is-expanded" : ""} />{showLogs ? "收起日志" : "查看日志"}</button></header>{showLogs && <div className="zc-opencode-log-list">{props.events.length ? props.events.map((event) => <article key={event.id}><span className={`zc-log-kind zc-log-kind-${event.event_type}`}>{eventKindLabel(event.event_type)}</span><p>{eventLabel(event)}</p><time>{event.created_at ? new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(event.created_at)) : ""}</time></article>) : <p className="zc-opencode-log-empty">任务启动后，模型、工具、校验和导出事件会显示在这里。</p>}</div>}</section>
  </div>;
}

function PreviewPanel(props: { project: Project; job: Job | null; activeArtifact: Artifact | null; activeOutline: OutlineSlide | null }) {
  const artifactBase = props.job && props.activeArtifact ? artifactUrl(props.project.id, props.job.id, props.activeArtifact.id) : "";
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef({ x: 0, y: 0, offsetX: 0, offsetY: 0 });

  useEffect(() => {
    setZoom(1);
    setOffset({ x: 0, y: 0 });
  }, [props.activeArtifact?.id]);

  function clampOffset(value: { x: number; y: number }, nextZoom = zoom) {
    const canvas = canvasRef.current;
    const width = canvas?.clientWidth || 700;
    const height = canvas?.clientHeight || 500;
    const maxX = nextZoom <= 1 ? 0 : ((nextZoom - 1) * width) / 2 + 80;
    const maxY = nextZoom <= 1 ? 0 : ((nextZoom - 1) * height) / 2 + 80;
    return { x: Math.max(-maxX, Math.min(maxX, value.x)), y: Math.max(-maxY, Math.min(maxY, value.y)) };
  }

  function updateZoom(nextZoom: number) {
    const bounded = Math.max(0.75, Math.min(2.5, nextZoom));
    setZoom(bounded);
    setOffset((current) => clampOffset(current, bounded));
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    updateZoom(zoom + (event.deltaY > 0 ? -0.1 : 0.1));
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (zoom <= 1 || event.button !== 0 || (event.target as HTMLElement).closest(".zc-preview-canvas-tools")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, offsetX: offset.x, offsetY: offset.y };
    setDragging(true);
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragging) return;
    setOffset(clampOffset({
      x: dragRef.current.offsetX + event.clientX - dragRef.current.x,
      y: dragRef.current.offsetY + event.clientY - dragRef.current.y,
    }));
  }

  function stopDragging(event: React.PointerEvent<HTMLDivElement>) {
    if (dragging && event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDragging(false);
  }

  return <div className="zc-preview-panel-rich"><div ref={canvasRef} className={`zc-preview-canvas-rich ${dragging ? "is-dragging" : ""}`} onWheel={handleWheel} onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={stopDragging} onPointerCancel={stopDragging}><div className="zc-preview-canvas-tools" role="toolbar" aria-label="预览画布工具"><button type="button" title="缩小" aria-label="缩小" onClick={() => updateZoom(zoom - 0.15)} disabled={zoom <= 0.75}><ZoomOut size={15} /></button><span aria-live="polite">{Math.round(zoom * 100)}%</span><button type="button" title="放大" aria-label="放大" onClick={() => updateZoom(zoom + 0.15)} disabled={zoom >= 2.5}><ZoomIn size={15} /></button><button type="button" title="重置视图" aria-label="重置视图" onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); }}><RotateCcw size={14} /></button></div>{artifactBase ? <img draggable={false} style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }} src={artifactBase} alt={props.activeOutline?.title || "演示文稿页面"} /> : <div><LayoutTemplate size={35} /><strong>正在整理页面预览</strong></div>}</div></div>;
}
