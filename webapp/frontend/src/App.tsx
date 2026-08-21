import { FormEvent, KeyboardEvent, UIEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PresentationEditor } from "./PresentationEditor";
import {
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Check,
  CircleAlert,
  Clock3,
  Copy,
  Download,
  Eye,
  FileText,
  LoaderCircle,
  LogIn,
  LogOut,
  Menu,
  PackageCheck,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Power,
  Send,
  Square,
  Settings,
  Trash2,
  UserPlus,
  UserRound,
  Wrench,
  X,
} from "lucide-react";

type User = { id: string; username: string; email: string; display_name: string; role: string };
type AdminUser = User & { is_active: boolean; deletion_pending: boolean };
type ManagedModel = { id: string; model_id: string; display_name: string; is_active: boolean; is_default: boolean };
type Provider = { id: string; slug: string; display_name: string; base_url: string; api_key_hint: string; is_active: boolean; models: ManagedModel[] };
type ModelCatalogEntry = {
  model_id: string;
  source: "environment" | "managed";
  provider_id: string | null;
  provider_display_name: string | null;
  is_available: boolean;
  is_default: boolean;
};
type Project = { id: string; title: string; created_at: string; updated_at: string };
type Job = {
  id: string;
  project_id: string;
  base_job_id: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  prompt: string;
  model: string | null;
  error: string | null;
  cancellation_requested: boolean;
  created_at: string;
};
type JobEvent = {
  id: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};
type Artifact = {
  id: string;
  kind: "svg" | "pptx" | "report";
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
};
type PreviewState = {
  download: Artifact;
  slides: Artifact[];
  index: number;
};
type ProjectTooltip = { projectId: string; title: string; top: number; left: number };
type ManagedDeletion =
  | { kind: "provider"; provider: Provider }
  | { kind: "model"; provider: Provider; model: ManagedModel };

const statusText: Record<Job["status"], string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const phaseText: Record<string, string> = {
  initializing: "正在准备工作区",
  continuing: "正在载入上一版演示文稿",
  running: "OpenCode 正在执行",
  succeeded: "已完成",
  failed: "执行失败",
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "请求失败，请稍后重试。");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

function eventText(event: JobEvent): string {
  const payload = event.payload;
  if (event.event_type === "status") {
    const status = String(payload.status || "");
    return phaseText[status] || `任务状态：${status || "处理中"}`;
  }
  if (event.event_type === "tool") {
    const tool = String(payload.tool || "工具");
    const detail = String(payload.detail || "");
    return detail ? `${tool} ${detail}` : tool;
  }
  if (event.event_type === "usage") return `本步骤完成，已使用 ${String(payload.tokens ?? 0)} tokens`;
  if (event.event_type === "artifact") return `已生成 ${String(payload.path || "产物")}`;
  if (event.event_type === "error") return String(payload.message || "执行失败");
  if (event.event_type === "permission") return `权限提示：${String(payload.message || "访问被拒绝")}`;
  if (event.event_type === "validation") {
    return String(payload.message || `${payload.continuation === true ? "修改" : "生成"}校验完成`);
  }
  return String(payload.text || payload.event || "执行中");
}

function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function isSvgArtifact(artifact: Artifact): boolean {
  return artifact.kind === "svg" || artifact.filename.toLowerCase().endsWith(".svg");
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [prompt, setPrompt] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [copiedPromptJobId, setCopiedPromptJobId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showExecution, setShowExecution] = useState(true);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);
  const [pendingProjectDeletion, setPendingProjectDeletion] = useState<Project | null>(null);
  const [projectTooltip, setProjectTooltip] = useState<ProjectTooltip | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"users" | "models">("users");
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [catalogModels, setCatalogModels] = useState<ModelCatalogEntry[]>([]);
  const [showAddUser, setShowAddUser] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [pendingManagedDeletion, setPendingManagedDeletion] = useState<ManagedDeletion | null>(null);
  const endOfConversationRef = useRef<HTMLDivElement | null>(null);
  const conversationScrollRef = useRef<HTMLDivElement | null>(null);
  const conversationFollowsLatestRef = useRef(true);
  const executionPanelRef = useRef<HTMLDivElement | null>(null);
  const executionFollowsLatestRef = useRef(true);
  const token = new URLSearchParams(window.location.search).get("invite");
  const editorRoute = /^\/editor\/([^/]+)\/([^/]+)$/.exec(window.location.pathname);

  const loadProjects = useCallback(async () => {
    const nextProjects = await request<Project[]>("/api/v1/projects");
    setProjects(nextProjects);
    setActiveProjectId((current) => current ?? nextProjects[0]?.id ?? null);
  }, []);

  const loadJobs = useCallback(async (projectId: string) => {
    const nextJobs = await request<Job[]>(`/api/v1/projects/${projectId}/jobs`);
    setJobs(nextJobs);
    setActiveJobId((current) => current ?? nextJobs[0]?.id ?? null);
  }, []);

  const loadActiveModel = useCallback(async () => {
    const configured = await request<Array<{ id: string }>>("/api/v1/models");
    setActiveModel(configured[0]?.id ?? null);
  }, []);

  const loadAdminSettings = useCallback(async () => {
    const [nextUsers, nextProviders, nextCatalog] = await Promise.all([
      request<AdminUser[]>("/api/v1/admin/users"),
      request<Provider[]>("/api/v1/admin/providers"),
      request<ModelCatalogEntry[]>("/api/v1/admin/model-catalog"),
    ]);
    setAdminUsers(nextUsers); setProviders(nextProviders); setCatalogModels(nextCatalog);
  }, []);

  useEffect(() => { if (settingsOpen && user?.role === "admin") void loadAdminSettings().catch((error) => setMessage(error instanceof Error ? error.message : "加载系统设置失败")); }, [loadAdminSettings, settingsOpen, user?.role]);

  const loadArtifacts = useCallback(async (projectId: string, jobId: string) => {
    setArtifacts(await request<Artifact[]>(`/api/v1/projects/${projectId}/jobs/${jobId}/artifacts`));
  }, []);

  useEffect(() => {
    request<User>("/api/v1/auth/me")
      .then(async (currentUser) => {
        setUser(currentUser);
        await Promise.all([loadProjects(), loadActiveModel()]);
      })
      .catch(() => undefined);
  }, [loadActiveModel, loadProjects]);

  useEffect(() => {
    if (!activeProjectId) {
      setJobs([]);
      setActiveJobId(null);
      return;
    }
    setActiveJobId(null);
    void loadJobs(activeProjectId);
    const timer = window.setInterval(() => void loadJobs(activeProjectId), 5_000);
    return () => window.clearInterval(timer);
  }, [activeProjectId, loadJobs]);

  useEffect(() => {
    if (!activeProjectId || !activeJobId) {
      setEvents([]);
      setArtifacts([]);
      return;
    }
    let disposed = false;
    void Promise.all([
      request<JobEvent[]>(`/api/v1/projects/${activeProjectId}/jobs/${activeJobId}/events`),
      loadArtifacts(activeProjectId, activeJobId),
    ]).then(([history]) => {
      if (!disposed) setEvents(history);
    }).catch(() => undefined);
    const stream = new EventSource(`/api/v1/projects/${activeProjectId}/jobs/${activeJobId}/events/stream`);
    let reconcileTimer: number | undefined;
    const scheduleReconcile = () => {
      window.clearTimeout(reconcileTimer);
      reconcileTimer = window.setTimeout(() => {
        if (disposed) return;
        void loadJobs(activeProjectId);
        void loadArtifacts(activeProjectId, activeJobId);
      }, 800);
    };
    stream.addEventListener("job-event", (rawEvent) => {
      const incoming = JSON.parse((rawEvent as MessageEvent).data) as JobEvent;
      setEvents((current) => current.some((event) => event.id === incoming.id) ? current : [...current, incoming]);
      if (incoming.event_type === "status") void loadJobs(activeProjectId);
      if (incoming.event_type === "artifact" || incoming.event_type === "status") {
        void loadArtifacts(activeProjectId, activeJobId);
      }
      if (incoming.event_type === "artifact" || incoming.event_type === "validation") scheduleReconcile();
    });
    stream.addEventListener("complete", () => {
      stream.close();
      void loadJobs(activeProjectId);
      void loadArtifacts(activeProjectId, activeJobId);
    });
    return () => {
      disposed = true;
      window.clearTimeout(reconcileTimer);
      stream.close();
    };
  }, [activeJobId, activeProjectId, loadArtifacts, loadJobs]);

  const conversationJobs = useMemo(() => [...jobs].reverse(), [jobs]);
  const activeProject = projects.find((project) => project.id === activeProjectId);
  const activeJob = jobs.find((job) => job.id === activeJobId);
  const runningJob = jobs.find((job) => job.status === "queued" || job.status === "running");
  const isConversationBusy = jobs.some((job) => job.status === "queued" || job.status === "running");
  const activeJobFinalizing = activeJob?.status === "running" && events.some((event) => event.event_type === "validation" && event.payload.passed === true);
  const uniqueArtifacts = useMemo(() => {
    const newestFirst = [...artifacts].sort((left, right) => right.created_at.localeCompare(left.created_at));
    const seen = new Set<string>();
    return newestFirst.filter((artifact) => {
      const key = `${artifact.kind}:${artifact.filename}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [artifacts]);
  const latestPptxId = uniqueArtifacts.find((artifact) => artifact.kind === "pptx")?.id;
  const deliveryArtifacts = uniqueArtifacts.filter(
    (artifact) => !isSvgArtifact(artifact) && (artifact.kind !== "pptx" || artifact.id === latestPptxId),
  );
  const sourceArtifacts = useMemo(
    () => uniqueArtifacts
      .filter(isSvgArtifact)
      .sort((left, right) => left.filename.localeCompare(right.filename, undefined, { numeric: true, sensitivity: "base" })),
    [uniqueArtifacts],
  );

  useEffect(() => {
    if (!preview) return;
    function handlePreviewKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") setPreview(null);
      if (event.key === "ArrowLeft") setPreview((current) => current ? { ...current, index: Math.max(0, current.index - 1) } : current);
      if (event.key === "ArrowRight") setPreview((current) => current ? { ...current, index: Math.min(current.slides.length - 1, current.index + 1) } : current);
    }
    window.addEventListener("keydown", handlePreviewKeyDown);
    return () => window.removeEventListener("keydown", handlePreviewKeyDown);
  }, [preview]);

  useEffect(() => {
    conversationFollowsLatestRef.current = true;
  }, [activeProjectId]);

  useEffect(() => {
    if (conversationJobs.length > 0 && conversationFollowsLatestRef.current) {
      endOfConversationRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [activeProjectId, conversationJobs.length]);

  useEffect(() => {
    executionFollowsLatestRef.current = true;
  }, [activeJobId]);

  useEffect(() => {
    const panel = executionPanelRef.current;
    if (!panel || !showExecution || !executionFollowsLatestRef.current) return;
    panel.scrollTop = panel.scrollHeight;
  }, [activeJobId, events, showExecution]);

  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    try {
      if (token) {
        await request<User>("/api/v1/auth/register", {
          method: "POST",
          body: JSON.stringify({ token, username, display_name: displayName, password }),
        });
        setMessage("账号已创建，请返回登录。");
        return;
      }
      setUser(await request<User>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      }));
      await Promise.all([loadProjects(), loadActiveModel()]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "请求失败，请稍后重试。");
    }
  }

  async function createProject(title: string): Promise<Project> {
    const project = await request<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    setProjects((current) => [project, ...current]);
    setActiveProjectId(project.id);
    return project;
  }

  function showProjectTooltip(project: Project, element: HTMLElement) {
    const rect = element.getBoundingClientRect();
    setProjectTooltip({ projectId: project.id, title: project.title, top: rect.top + rect.height / 2, left: rect.right + 8 });
  }

  function hideProjectTooltip(projectId: string) {
    setProjectTooltip((current) => current?.projectId === projectId ? null : current);
  }

  async function deleteProject(project: Project) {
    setDeletingProjectId(project.id);
    setPendingProjectDeletion(null);
    setMessage("");
    try {
      await request<void>(`/api/v1/projects/${project.id}`, { method: "DELETE" });
      const nextProjects = await request<Project[]>("/api/v1/projects");
      setProjects(nextProjects);
      setActiveProjectId((current) => current === project.id ? nextProjects[0]?.id ?? null : current);
      if (activeProjectId === project.id) {
        setActiveJobId(null);
        setJobs([]);
      }
      setSidebarOpen(false);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "删除对话失败，请稍后重试。";
      setMessage(
        detail === "当前对话仍有任务在执行，请等待完成后再删除。"
          ? "当前任务仍在执行。请先点击“中止任务”，或等待任务结束后再删除。"
          : detail,
      );
    } finally {
      setDeletingProjectId(null);
    }
  }

  function openPreview(download: Artifact, slides: Artifact[]) {
    setPreview({ download, slides, index: 0 });
  }

  function startNewConversation() {
    setActiveProjectId(null);
    setActiveJobId(null);
    setPrompt("");
    setMessage("");
    setSidebarOpen(false);
  }

  async function cancelRunningJob() {
    if (!activeProjectId || !runningJob || runningJob.cancellation_requested) return;
    setMessage("");
    try {
      const updatedJob = await request<Job>(
        `/api/v1/projects/${activeProjectId}/jobs/${runningJob.id}/cancel`,
        { method: "POST" },
      );
      setJobs((current) => current.map((job) => job.id === updatedJob.id ? updatedJob : job));
      setMessage(updatedJob.status === "cancelled" ? "任务已取消。" : "正在中止当前任务…");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "中止任务失败，请稍后重试。");
    }
  }

  async function submitGeneration(event?: FormEvent) {
    event?.preventDefault();
    const text = prompt.trim();
    if (!text || isSubmitting || isConversationBusy) return;
    setIsSubmitting(true);
    setMessage("");
    try {
      let projectId = activeProjectId;
      if (!projectId) {
        const project = await createProject(text.slice(0, 32));
        projectId = project.id;
      }
      const job = await request<Job>(`/api/v1/projects/${projectId}/jobs`, {
        method: "POST",
      body: JSON.stringify({ prompt: text }),
      });
      setJobs((current) => [job, ...current]);
      setActiveJobId(job.id);
      setShowExecution(true);
      setPrompt("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建生成任务失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitGeneration();
    }
  }

  function toggleExecution() {
    setShowExecution((current) => {
      if (!current) executionFollowsLatestRef.current = true;
      return !current;
    });
  }

  function handleExecutionScroll(event: UIEvent<HTMLDivElement>) {
    const panel = event.currentTarget;
    executionFollowsLatestRef.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 20;
  }

  function handleConversationScroll(event: UIEvent<HTMLDivElement>) {
    const panel = event.currentTarget;
    conversationFollowsLatestRef.current = panel.scrollHeight - panel.scrollTop - panel.clientHeight <= 24;
  }

  async function logout() {
    await request<void>("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
    setProjects([]);
    setJobs([]);
    setActiveProjectId(null);
    setActiveJobId(null);
    setActiveModel(null);
  }

  async function addUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    if (values.get("password") !== values.get("password_confirmation")) {
      setMessage("两次输入的密码不一致");
      return;
    }
    try {
      await request<AdminUser>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(Object.fromEntries(values)) });
      setShowAddUser(false); await loadAdminSettings();
    } catch (error) { setMessage(error instanceof Error ? error.message : "添加用户失败"); }
  }

  async function deleteUser(target: AdminUser) {
    if (!window.confirm(`删除“${target.display_name}”将中止其任务并删除全部项目与产物。是否继续？`)) return;
    try { await request<AdminUser>(`/api/v1/admin/users/${target.id}`, { method: "DELETE" }); await loadAdminSettings(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "删除用户失败"); }
  }

  async function addProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));
    try { await request<Provider>("/api/v1/admin/providers", { method: "POST", body: JSON.stringify(payload) }); form.reset(); await loadAdminSettings(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "新增 Provider 失败"); }
  }

  async function addManagedModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form)); const providerId = String(values.provider_id);
    const payload = { model_id: String(values.model_id), display_name: String(values.display_name), is_active: true };
    try { await request<ManagedModel>(`/api/v1/admin/providers/${providerId}/models`, { method: "POST", body: JSON.stringify(payload) }); form.reset(); await loadAdminSettings(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "新增模型失败"); }
  }

  async function updateProvider(provider: Provider, payload: Record<string, unknown>) {
    try {
      await request<Provider>(`/api/v1/admin/providers/${provider.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      setEditingProvider(null);
      await loadAdminSettings();
    } catch (error) { setMessage(error instanceof Error ? error.message : "更新 Provider 失败"); }
  }

  async function submitProviderEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingProvider) return;
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const apiKey = String(values.api_key || "").trim();
    const payload: Record<string, unknown> = {
      display_name: String(values.display_name),
      base_url: String(values.base_url),
      is_active: values.is_active === "true",
    };
    if (apiKey) payload.api_key = apiKey;
    await updateProvider(editingProvider, payload);
  }

  async function updateManagedModel(managedModel: ManagedModel, payload: Record<string, unknown>) {
    try {
      await request<ManagedModel>(`/api/v1/admin/models/${managedModel.id}`, { method: "PATCH", body: JSON.stringify(payload) });
      await loadAdminSettings();
    } catch (error) { setMessage(error instanceof Error ? error.message : "更新模型失败"); }
  }

  async function updateDefaultModel(entry: ModelCatalogEntry) {
    try {
      await request<ModelCatalogEntry[]>("/api/v1/admin/model-catalog/default", {
        method: "PATCH",
        body: JSON.stringify({ model_id: entry.model_id }),
      });
      await Promise.all([loadAdminSettings(), loadActiveModel()]);
    } catch (error) { setMessage(error instanceof Error ? error.message : "激活模型失败"); }
  }

  async function deleteProvider(provider: Provider) {
    setPendingManagedDeletion({ kind: "provider", provider });
  }

  async function deleteManagedModel(provider: Provider, managedModel: ManagedModel) {
    setPendingManagedDeletion({ kind: "model", provider, model: managedModel });
  }

  async function confirmManagedDeletion() {
    const target = pendingManagedDeletion;
    if (!target) return;
    try {
      const path = target.kind === "provider"
        ? `/api/v1/admin/providers/${target.provider.id}`
        : `/api/v1/admin/models/${target.model.id}`;
      await request<void>(path, { method: "DELETE" });
      setPendingManagedDeletion(null);
      await Promise.all([loadAdminSettings(), loadActiveModel()]);
    } catch (error) { setMessage(error instanceof Error ? error.message : "删除失败"); }
  }

  if (!user) {
    return (
      <main className="shell">
        <section className="auth-panel">
          <div className="brand"><img className="brand-logo" src="/logo-128.png" alt="" aria-hidden="true" /><span>智创PPT专家</span></div>
          <h1>{token ? "创建你的账户" : "创作演示文稿"}</h1>
          <form onSubmit={submitAuth}>
            <label>账号<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></label>
            {token && <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>}
            <label>密码<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete={token ? "new-password" : "current-password"} minLength={8} required /></label>
            <button type="submit">{token ? <UserPlus size={17} /> : <LogIn size={17} />}{token ? "完成注册" : "登录"}</button>
          </form>
          {message && <p className="message">{message}</p>}
        </section>
      </main>
    );
  }

  if (editorRoute) {
    return <PresentationEditor projectId={editorRoute[1]} jobId={editorRoute[2]} />;
  }

  const renderComposer = (compact: boolean) => (
    <form className={`composer ${compact ? "composer-docked" : "composer-centered"} ${isConversationBusy ? "composer-busy" : ""}`} onSubmit={submitGeneration}>
      <textarea
        aria-label="PPT 需求"
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={handleComposerKeyDown}
        placeholder={isConversationBusy ? activeJobFinalizing ? "正在整理演示文稿产物…" : "当前任务正在执行，完成后可继续修改" : activeProject ? "继续修改这份演示文稿…" : "描述你要生成的 PPT，例如：制作一份新能源汽车市场的 8 页简报"}
        rows={compact ? 2 : 4}
        disabled={isConversationBusy}
      />
      <div className="composer-actions">
        {isConversationBusy ? (
          <button className="cancel-job-button" type="button" onClick={() => void cancelRunningJob()} disabled={!runningJob || runningJob.cancellation_requested} title="中止当前任务">
            {runningJob?.cancellation_requested ? <LoaderCircle className="spin" size={16} /> : <Square size={15} fill="currentColor" />}<span>{runningJob?.cancellation_requested ? "正在中止" : "中止任务"}</span>
          </button>
        ) : (
          <button className="send-button" type="submit" title="发送需求" aria-label="发送需求" disabled={!prompt.trim() || isSubmitting}>
            {isSubmitting ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}
          </button>
        )}
      </div>
    </form>
  );

  const copyPrompt = async (job: Job) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(job.prompt);
      } else {
        const textArea = document.createElement("textarea");
        textArea.value = job.prompt;
        textArea.style.position = "fixed";
        textArea.style.opacity = "0";
        document.body.append(textArea);
        textArea.select();
        const copied = document.execCommand("copy");
        textArea.remove();
        if (!copied) throw new Error("copy failed");
      }
      setCopiedPromptJobId(job.id);
      window.setTimeout(() => setCopiedPromptJobId((current) => current === job.id ? null : current), 1600);
    } catch {
      setMessage("无法复制需求，请检查浏览器剪贴板权限。");
    }
  };

  const artifactUrl = (jobId: string, artifactId: string) => `/api/v1/projects/${activeProjectId}/jobs/${jobId}/artifacts/${artifactId}/download`;
  const renderArtifactCard = (job: Job, artifact: Artifact, slides: Artifact[]) => (
    <div className="artifact-link" key={artifact.id}>
      <button className="artifact-preview-button" type="button" onClick={() => openPreview(artifact, slides)}>
        <Eye size={16} />
        <span><strong>{artifact.kind === "pptx" ? "演示文稿" : "SVG 源文件"}</strong><small>{formatSize(artifact.size_bytes)}</small></span>
      </button>
      {artifact.kind === "pptx" && <button className="artifact-edit-button" type="button" title="手动编辑演示文稿" aria-label="手动编辑演示文稿" onClick={() => window.location.assign(`/editor/${activeProjectId}/${job.id}`)}><Pencil size={16} /><span>手动编辑</span></button>}
      <a className="artifact-download-button" href={artifactUrl(job.id, artifact.id)} title={`下载 ${artifact.filename}`} aria-label={`下载 ${artifact.filename}`}><Download size={16} /><span>下载文稿</span></a>
    </div>
  );

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${sidebarOpen ? "sidebar-open" : ""} ${settingsOpen ? "settings-open" : ""}`}>
      <button className="mobile-sidebar-toggle" type="button" aria-label="打开历史记录" onClick={() => setSidebarOpen(true)}><Menu size={20} /></button>
      <aside className="sidebar" aria-label="历史对话">
        <div className="sidebar-top">
          <div className="sidebar-header">
            <div className="brand"><img className="brand-logo" src="/logo-128.png" alt="" aria-hidden="true" /><span>智创PPT专家</span></div>
            <button className="icon-button sidebar-collapse" type="button" title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"} onClick={() => setSidebarCollapsed((current) => !current)}>
              {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
            <button className="icon-button mobile-sidebar-close" type="button" aria-label="关闭历史记录" onClick={() => setSidebarOpen(false)}><X size={18} /></button>
          </div>
          <button className="new-chat-button" type="button" onClick={startNewConversation} title="新建对话"><Plus size={17} /><span>新建对话</span></button>
        </div>
        <nav className="project-list" aria-label="项目列表">
          <p className="sidebar-section-title">历史记录</p>
          {projects.map((project) => (
            <div className="project-entry" key={project.id}>
              <button className={`project-item ${project.id === activeProjectId ? "active" : ""}`} type="button" aria-label={project.title} onMouseEnter={(event) => showProjectTooltip(project, event.currentTarget)} onMouseLeave={() => hideProjectTooltip(project.id)} onFocus={(event) => showProjectTooltip(project, event.currentTarget)} onBlur={() => hideProjectTooltip(project.id)} onClick={() => { setActiveProjectId(project.id); setSidebarOpen(false); }}>
                <FileText size={16} />
                <span className="project-item-title">{project.title}</span>
                {!sidebarCollapsed && <span className="project-item-date">{shortDate(project.updated_at)}</span>}
              </button>
              <button className="project-delete" type="button" title={`删除 ${project.title}`} aria-label={`删除 ${project.title}`} disabled={deletingProjectId === project.id} onClick={() => setPendingProjectDeletion(project)}>
                {deletingProjectId === project.id ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}
              </button>
            </div>
          ))}
          {projects.length === 0 && <p className="sidebar-empty">从一份新的演示文稿开始</p>}
        </nav>
        <div className="account-row">
          <span className="account-avatar">{user.display_name.slice(0, 1).toUpperCase()}</span>
          <span className="account-name">{user.display_name}</span>
          {user.role === "admin" && <button className="icon-button admin-settings-button" type="button" title="系统设置" aria-label="系统设置" onClick={() => { setSettingsTab("users"); setSettingsOpen(true); }}><Settings size={17} /></button>}
          <button className="icon-button" type="button" title="退出登录" aria-label="退出登录" onClick={logout}><LogOut size={17} /></button>
        </div>
      </aside>
      {projectTooltip && <div className="project-title-tooltip" role="tooltip" style={{ left: projectTooltip.left, top: projectTooltip.top }}>{projectTooltip.title}</div>}
      {sidebarOpen && <button className="sidebar-scrim" type="button" aria-label="关闭历史记录" onClick={() => setSidebarOpen(false)} />}
      <main className="workspace">
        {settingsOpen ? <section className="settings-page">
          <header className="settings-header"><div><p>管理员控制台</p><h1>系统设置</h1></div><button className="icon-button settings-close" type="button" title="返回工作台" aria-label="返回工作台" onClick={() => setSettingsOpen(false)}><X size={18} /></button></header>
          <nav className="settings-tabs" role="tablist" aria-label="系统设置分类"><button className={settingsTab === "users" ? "active" : ""} type="button" role="tab" aria-selected={settingsTab === "users"} onClick={() => setSettingsTab("users")}>用户管理</button><button className={settingsTab === "models" ? "active" : ""} type="button" role="tab" aria-selected={settingsTab === "models"} onClick={() => setSettingsTab("models")}>模型管理</button></nav>
          {message && <p className="message">{message}</p>}
          {settingsTab === "users" ? <section className="settings-section"><div className="settings-section-head"><div><h2>用户管理</h2><p>由管理员直接创建和维护平台账号。</p></div><button className="command-button" type="button" onClick={() => setShowAddUser(true)}><UserPlus size={16} />添加用户</button></div>
            <div className="settings-table"><div className="settings-row settings-table-head"><span>用户</span><span>角色</span><span>状态</span><span>操作</span></div>{adminUsers.map((item) => <div className="settings-row" key={item.id}><span><strong>{item.display_name}</strong><small>{item.username}</small></span><span>{item.role === "admin" ? "管理员" : "普通用户"}</span><span>{item.deletion_pending ? "删除中" : item.is_active ? "启用" : "已停用"}</span><span>{item.id !== (user as AdminUser).id && <button className="text-danger" type="button" onClick={() => void deleteUser(item)}>删除</button>}</span></div>)}</div>
          </section> : <section className="settings-section"><div className="settings-section-head"><div><h2>模型管理</h2><p>Provider 采用 OpenAI-compatible 协议，API Key 以密文保存。</p></div></div>
            <div className="available-models" aria-label="当前激活模型"><h3>当前激活模型</h3><p>平台每次生成仅使用一个模型。管理员在此切换全局激活模型，普通用户不能自行选择。</p>{catalogModels.length > 0 ? <div className="available-model-list">{catalogModels.map((item) => <div className={`available-model-row ${!item.is_available ? "is-unavailable" : ""}`} key={item.model_id}><span><strong>{item.model_id}</strong><small>{item.source === "managed" ? `受管 Provider · ${item.provider_display_name || "未命名"}` : "环境配置（只读）"}</small></span><label className="active-model-toggle" title={item.is_available ? "设为全局激活模型" : "Provider 或模型已停用"}><input type="radio" name="active-model" checked={item.is_default} disabled={!item.is_available} onChange={() => void updateDefaultModel(item)} /><span>{item.is_default ? "当前激活" : "激活此模型"}</span></label></div>)}</div> : <p className="settings-empty">尚未发现可用模型。</p>}</div>
            <form className="settings-form provider-form" onSubmit={addProvider}><input name="slug" placeholder="Provider 标识，例如 aihub" required /><input name="display_name" placeholder="Provider 名称" required /><input name="base_url" type="url" placeholder="https://api.example.com/v1" required /><input name="api_key" type="password" placeholder="API Key" required /><button className="command-button" type="submit"><Plus size={16} />新增 Provider</button></form>
            <form className="settings-form model-form" onSubmit={addManagedModel}><select name="provider_id" required defaultValue=""><option value="" disabled>选择 Provider</option>{providers.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select><input name="model_id" placeholder="模型 ID" required /><input name="display_name" placeholder="显示名称" required /><button className="command-button" type="submit"><Plus size={16} />新增模型</button></form>
            <div className="settings-table"><div className="settings-row settings-table-head model-table-head"><span>Provider</span><span>模型</span><span>凭据</span><span>状态</span><span>操作</span></div>{providers.map((provider) => <div key={provider.id} className="provider-group"><div className="settings-row model-table-head provider-row"><span><strong>{provider.display_name}</strong><small>{provider.slug} · {provider.base_url}</small></span><span>Provider</span><span>{provider.api_key_hint}</span><span>{provider.is_active ? "启用" : "停用"}</span><span className="settings-actions"><button className="icon-button light-icon" type="button" title="编辑 Provider" aria-label={`编辑 ${provider.display_name}`} onClick={() => setEditingProvider(provider)}><Pencil size={15} /></button><button className="icon-button light-icon" type="button" title={provider.is_active ? "停用 Provider" : "启用 Provider"} aria-label={provider.is_active ? `停用 ${provider.display_name}` : `启用 ${provider.display_name}`} onClick={() => void updateProvider(provider, { is_active: !provider.is_active })}><Power size={15} /></button><button className="icon-button light-icon danger-icon" type="button" title="删除 Provider" aria-label={`删除 ${provider.display_name}`} onClick={() => void deleteProvider(provider)}><Trash2 size={15} /></button></span></div>{provider.models.map((item) => <div className="settings-row model-table-head model-row" key={item.id}><span><small>{provider.display_name}</small></span><span><strong>{provider.slug}/{item.model_id}</strong><small>{item.display_name}</small></span><span>{provider.api_key_hint}</span><span>{provider.is_active && item.is_active ? "启用" : "停用"}</span><span className="settings-actions"><button className="icon-button light-icon" type="button" title={item.is_active ? "停用模型" : "启用模型"} aria-label={item.is_active ? `停用 ${item.display_name}` : `启用 ${item.display_name}`} onClick={() => void updateManagedModel(item, { is_active: !item.is_active })}><Power size={15} /></button><button className="icon-button light-icon danger-icon" type="button" title="删除模型" aria-label={`删除 ${item.display_name}`} onClick={() => void deleteManagedModel(provider, item)}><Trash2 size={15} /></button></span></div>)}</div>)}</div>
          </section>
          }
          {showAddUser && <div className="preview-backdrop" role="presentation"><form className="settings-modal" onSubmit={addUser}><header><h2>添加用户</h2><button className="icon-button" type="button" aria-label="关闭" onClick={() => setShowAddUser(false)}><X size={18} /></button></header><label>账号<input name="username" autoComplete="username" required minLength={3} /></label><label>密码<input name="password" type="password" autoComplete="new-password" required minLength={8} /></label><label>确认密码<input name="password_confirmation" type="password" autoComplete="new-password" required minLength={8} /></label><button className="command-button" type="submit">创建用户</button></form></div>}
          {editingProvider && <div className="preview-backdrop" role="presentation"><form className="settings-modal" onSubmit={submitProviderEdit}><header><h2>编辑 Provider</h2><button className="icon-button" type="button" aria-label="关闭" onClick={() => setEditingProvider(null)}><X size={18} /></button></header><label>Provider 标识<input value={editingProvider.slug} disabled /></label><label>显示名称<input name="display_name" defaultValue={editingProvider.display_name} required /></label><label>Base URL<input name="base_url" type="url" defaultValue={editingProvider.base_url} required /></label><label>替换 API Key<input name="api_key" type="password" placeholder="留空表示不修改" /></label><label>状态<select name="is_active" defaultValue={String(editingProvider.is_active)}><option value="true">启用</option><option value="false">停用</option></select></label><button className="command-button" type="submit">保存更改</button></form></div>}
          {pendingManagedDeletion && <div className="preview-backdrop" role="presentation"><section className="settings-modal deletion-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-managed-title"><header><h2 id="delete-managed-title">确认删除</h2><button className="icon-button" type="button" aria-label="关闭" onClick={() => setPendingManagedDeletion(null)}><X size={18} /></button></header><p>{pendingManagedDeletion.kind === "provider" ? `将删除 Provider“${pendingManagedDeletion.provider.display_name}”及其全部模型。` : `将删除模型“${pendingManagedDeletion.provider.slug}/${pendingManagedDeletion.model.model_id}”。`}</p><p>该操作不可撤销；如有排队或执行中的任务，系统会阻止删除以保护任务。</p><div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setPendingManagedDeletion(null)}>取消</button><button className="command-button danger-command" type="button" onClick={() => void confirmManagedDeletion()}>确认删除</button></div></section></div>}
        </section> : <>
        <header className="workspace-header">
          <div className="conversation-heading"><p>{activeProject ? "当前对话" : "智创PPT专家"}</p><h1>{activeProject?.title ?? "一句话，开始一份演示文稿"}</h1></div>
          {activeJob?.model && <span className="header-model">{activeJob.model}</span>}
        </header>
        <div className={`conversation-scroll ${conversationJobs.length === 0 ? "conversation-empty" : ""}`} ref={conversationScrollRef} onScroll={handleConversationScroll}>
          {conversationJobs.length === 0 ? (
            <section className="welcome-state">
              <img className="welcome-mark" src="/logo-128.png" alt="" aria-hidden="true" />
              <h2>今天想制作什么演示文稿？</h2>
              <p>从一个主题开始，之后可以持续修改内容、结构与视觉风格。</p>
              <div className="active-engine" aria-label="当前模型">当前模型 <code>{activeModel ?? "尚未配置"}</code></div>
              {renderComposer(false)}
              {message && <p className="message">{message}</p>}
            </section>
          ) : (
            <section className="message-list" aria-label="对话内容">
              {conversationJobs.map((job) => {
                const isSelected = job.id === activeJobId;
                const isWorking = job.status === "queued" || job.status === "running";
                const validationEvent = [...events].reverse().find((event) => event.event_type === "validation");
                const isFinalizing = isSelected && isWorking && validationEvent?.payload.passed === true;
                const validationType = validationEvent?.payload.continuation === true ? "修改" : "生成";
                const validationTitle = validationEvent?.payload.passed ? `${validationType}校验通过` : `${validationType}未生效`;
                const validationMessage = String(validationEvent?.payload.message || "").trim();
                const displayEvents = events.filter((event, index) => {
                  const previous = events[index - 1];
                  return !(previous && eventText(event) === "已完成" && eventText(previous) === "已完成");
                });
                return (
                  <article className="turn" key={job.id}>
                    <div className="message user-message"><span className="message-avatar"><UserRound size={16} /></span><div><p className="message-role">你</p><p>{job.prompt}</p></div><button className="message-copy-button" type="button" title={copiedPromptJobId === job.id ? "已复制" : "复制需求"} aria-label={copiedPromptJobId === job.id ? "已复制" : "复制需求"} onClick={() => void copyPrompt(job)}>{copiedPromptJobId === job.id ? <Check size={15} /> : <Copy size={15} />}</button></div>
                    <div className="message assistant-message">
                      <span className={`message-avatar assistant-avatar ${isWorking ? "is-working" : ""} ${isFinalizing ? "is-finalizing" : ""}`}>{isFinalizing ? <PackageCheck size={17} /> : isWorking ? <LoaderCircle className="spin" size={17} /> : <Bot size={17} />}</span>
                      <div className="assistant-content">
                        <div className="assistant-title-row"><p className="message-role">智创PPT专家</p><button className={`status-badge ${isFinalizing ? "finalizing" : job.status}`} type="button" onClick={() => setActiveJobId(job.id)}>{job.status === "failed" && <CircleAlert size={13} />}{job.status === "queued" && <Clock3 size={13} />}{isFinalizing ? "正在整理产物" : statusText[job.status]}</button></div>
                        {job.base_job_id && <p className="revision-note">基于上一版演示文稿继续修改</p>}
                        {job.error && <p className="job-error"><CircleAlert size={14} />{job.error}</p>}
                        {isSelected ? (
                          <>
                            <button className="execution-toggle" type="button" onClick={toggleExecution} aria-expanded={showExecution}><span><Wrench size={15} />OpenCode 执行过程</span>{showExecution ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</button>
                            {showExecution && <div className="execution-panel" aria-live="polite" ref={executionPanelRef} onScroll={handleExecutionScroll}>{displayEvents.length === 0 && <p className="empty-log">正在连接执行流…</p>}{displayEvents.map((event) => <p className={`event-line ${event.event_type}`} key={event.id}>{eventText(event)}</p>)}</div>}
                            {validationEvent && <div className={`validation-summary ${validationEvent.payload.passed ? "passed" : "failed"}`}><strong>{validationTitle}</strong>{validationMessage && validationMessage !== validationTitle && <span>{validationMessage}</span>}{Array.isArray(validationEvent.payload.checks) && validationEvent.payload.checks.map((check, index) => { const item = check as Record<string, unknown>; return <span key={index}>第 {String(item.page || "目标")} 页：{item.passed ? "已验证" : "未通过"}</span>; })}</div>}
                            {uniqueArtifacts.length > 0 && <div className="artifact-list" aria-label="本轮产物">
                              {deliveryArtifacts.map((artifact) => renderArtifactCard(job, artifact, sourceArtifacts))}
                            </div>}
                          </>
                        ) : <button className="view-turn-button" type="button" onClick={() => { setActiveJobId(job.id); setShowExecution(true); }}>查看本轮执行记录</button>}
                      </div>
                    </div>
                  </article>
                );
              })}
              <div ref={endOfConversationRef} />
            </section>
          )}
        </div>
        {conversationJobs.length > 0 && <div className="composer-dock-wrap">{message && <p className="composer-notice" role="alert"><CircleAlert size={16} />{message}</p>}{renderComposer(true)}</div>}
      {preview && activeProjectId && <div className="preview-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setPreview(null); }}>
        <section className="preview-dialog" role="dialog" aria-modal="true" aria-label={`${preview.download.filename}预览`}>
          <header className="preview-header">
            <div><p>预览</p><h2>{preview.download.kind === "pptx" ? "演示文稿预览" : preview.download.filename}</h2></div>
            <button className="icon-button preview-close" type="button" title="关闭预览" aria-label="关闭预览" onClick={() => setPreview(null)}><X size={19} /></button>
          </header>
          <div className="preview-body">
            {preview.slides.length > 1 && <div className="preview-thumbnails" aria-label="幻灯片列表">{preview.slides.map((slide, index) => <button className={`preview-thumbnail ${index === preview.index ? "active" : ""}`} type="button" key={slide.id} onClick={() => setPreview((current) => current ? { ...current, index } : current)}><img src={artifactUrl(activeJobId ?? "", slide.id)} alt={`第 ${index + 1} 页`} /><span>{index + 1}</span></button>)}</div>}
            <div className="preview-canvas">
              {preview.slides.length > 0 ? <img src={artifactUrl(activeJobId ?? "", preview.slides[preview.index].id)} alt={`${preview.download.filename}第 ${preview.index + 1} 页`} /> : <p>暂无可预览页面</p>}
            </div>
          </div>
          <footer className="preview-footer">
            <span>{preview.slides.length > 1 ? `第 ${preview.index + 1} / ${preview.slides.length} 页` : "SVG 页面"}</span>
            <div><button className="preview-nav" type="button" title="上一页" aria-label="上一页" disabled={preview.index === 0} onClick={() => setPreview((current) => current ? { ...current, index: Math.max(0, current.index - 1) } : current)}><ChevronLeft size={17} /></button><button className="preview-nav" type="button" title="下一页" aria-label="下一页" disabled={preview.index >= preview.slides.length - 1} onClick={() => setPreview((current) => current ? { ...current, index: Math.min(current.slides.length - 1, current.index + 1) } : current)}><ChevronRight size={17} /></button>{preview.slides.length > 0 && <a className="preview-svg-download" href={artifactUrl(activeJobId ?? "", preview.slides[preview.index].id)} title="下载当前页 SVG" aria-label="下载当前页 SVG" download><Download size={16} /><span>下载当前页 SVG</span></a>}<a className="preview-download" href={artifactUrl(activeJobId ?? "", preview.download.id)} download>下载文件</a></div>
          </footer>
        </section>
      </div>}
        </>}
      </main>
      {pendingProjectDeletion && <div className="preview-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setPendingProjectDeletion(null); }}>
        <section className="settings-modal deletion-modal project-delete-modal" role="alertdialog" aria-modal="true" aria-labelledby="delete-project-title">
          <header><h2 id="delete-project-title">确认删除对话</h2><button className="icon-button" type="button" aria-label="关闭" onClick={() => setPendingProjectDeletion(null)}><X size={18} /></button></header>
          <p className="deletion-target">{pendingProjectDeletion.title}</p>
          <p>将删除这条对话及其全部生成记录和文件。该操作不可撤销。</p>
          <div className="modal-actions"><button className="secondary-button" type="button" onClick={() => setPendingProjectDeletion(null)}>取消</button><button className="command-button danger-command" type="button" onClick={() => void deleteProject(pendingProjectDeletion)}>确认删除</button></div>
        </section>
      </div>}
    </div>
  );
}
