import { ChangeEvent, Dispatch, FormEvent, ReactNode, SetStateAction, useCallback, useEffect, useMemo, useState } from "react";
import { PresentationEditor } from "./PresentationEditor";
import { CreativeWorkspace } from "./CreativeWorkspace";
import {
  ArrowRight,
  ArrowLeft,
  Check,
  ChevronDown,
  Copy,
  Download,
  Edit3,
  Eye,
  Filter,
  FileDown,
  FilePlus2,
  FileStack,
  LayoutGrid,
  LayoutTemplate,
  ListFilter,
  List,
  LoaderCircle,
  LogIn,
  LogOut,
  Menu,
  MoreHorizontal,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Pencil,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Upload,
  UserPlus,
  UserRound,
  Users,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";

type User = { id: string; username: string; display_name: string; role: string; is_active: boolean; deletion_pending: boolean };
type Project = { id: string; title: string; created_at: string; updated_at: string };
type ProjectMaterial = { id: string; original_filename: string; content_type: string; size_bytes: number; status: "processing" | "ready" | "failed"; metadata: Record<string, unknown>; error: string | null; created_at: string; updated_at: string };
type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
type Job = {
  id: string;
  project_id: string;
  base_job_id: string | null;
  target_slide_number: number | null;
  template_id: string | null;
  template_name: string | null;
  status: JobStatus;
  prompt: string;
  error: string | null;
  cancellation_requested: boolean;
  created_at: string;
};
type Template = {
  id: string;
  name: string;
  original_filename: string;
  status: "analyzing" | "ready" | "failed";
  page_count: number | null;
  metadata: Record<string, unknown>;
  error: string | null;
  created_at: string;
  scope: "user" | "system";
  is_active: boolean;
  sort_order: number;
};
type PromptSnippet = {
  id: string;
  name: string;
  content: string;
  category: string;
  used_count: number;
  scope: "user" | "system";
  is_active: boolean;
  sort_order: number;
};
type Artifact = { id: string; kind: "svg" | "pptx" | "report"; filename: string; size_bytes: number; created_at: string };
type JobEvent = { id: number; event_type: string; payload: Record<string, unknown>; created_at: string };
type NavKey = "projects" | "templates" | "prompts" | "admin";
type ModalState = "project-delete" | "template-rename" | "prompt-editor" | "prompt-delete" | null;

const statusLabel: Record<JobStatus, string> = {
  queued: "等待执行",
  running: "生成中",
  succeeded: "已完成",
  failed: "生成失败",
  cancelled: "已中止",
};

const workbenchStages = ["需求梳理", "大纲设计", "选择模板", "生成 PPT", "预览精修"];

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

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function artifactUrl(jobId: string, artifactId: string): string {
  return `/api/v1/projects/${jobId.split(":")[0]}/jobs/${jobId.split(":")[1]}/artifacts/${artifactId}/download`;
}

export function ProductApp() {
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [nav, setNav] = useState<NavKey>("projects");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [darkMode, setDarkMode] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [globalQuery, setGlobalQuery] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [jobsByProject, setJobsByProject] = useState<Record<string, Job[]>>({});
  const [materialsByProject, setMaterialsByProject] = useState<Record<string, ProjectMaterial[]>>({});
  const [pendingMaterialFiles, setPendingMaterialFiles] = useState<File[]>([]);
  const [materialUploading, setMaterialUploading] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [snippets, setSnippets] = useState<PromptSnippet[]>([]);
  const [adminTemplates, setAdminTemplates] = useState<Template[]>([]);
  const [adminSnippets, setAdminSnippets] = useState<PromptSnippet[]>([]);
  const [adminUsers, setAdminUsers] = useState<User[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [projectQuery, setProjectQuery] = useState("");
  const [projectView, setProjectView] = useState<"grid" | "list">("grid");
  const [templateQuery, setTemplateQuery] = useState("");
  const [promptQuery, setPromptQuery] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [showLogs, setShowLogs] = useState(false);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [targetProject, setTargetProject] = useState<Project | null>(null);
  const [targetTemplate, setTargetTemplate] = useState<Template | null>(null);
  const [targetSnippet, setTargetSnippet] = useState<PromptSnippet | null>(null);
  const [editorName, setEditorName] = useState("");
  const [editorContent, setEditorContent] = useState("");
  const [editorCategory, setEditorCategory] = useState("个人");
  const [templateUploading, setTemplateUploading] = useState(false);
  const token = new URLSearchParams(window.location.search).get("invite");
  const editorRoute = /^\/editor\/([^/]+)\/([^/]+)$/.exec(window.location.pathname);

  const loadProjects = useCallback(async () => {
    const next = await request<Project[]>("/api/v1/projects");
    setProjects(next);
    const jobs = await Promise.all(next.map(async (project) => [project.id, await request<Job[]>(`/api/v1/projects/${project.id}/jobs`)] as const));
    setJobsByProject(Object.fromEntries(jobs));
  }, []);

  const loadTemplates = useCallback(async () => setTemplates(await request<Template[]>("/api/v1/templates")), []);
  const loadSnippets = useCallback(async () => setSnippets(await request<PromptSnippet[]>("/api/v1/prompt-snippets")), []);
  const loadMaterials = useCallback(async (projectId: string) => {
    const materials = await request<ProjectMaterial[]>(`/api/v1/projects/${projectId}/materials`);
    setMaterialsByProject((current) => ({ ...current, [projectId]: materials }));
    return materials;
  }, []);
  const loadAdminData = useCallback(async () => {
    const [nextTemplates, nextSnippets, nextUsers] = await Promise.all([
      request<Template[]>("/api/v1/admin/system-templates"),
      request<PromptSnippet[]>("/api/v1/admin/system-prompts"),
      request<User[]>("/api/v1/admin/users"),
    ]);
    setAdminTemplates(nextTemplates);
    setAdminSnippets(nextSnippets);
    setAdminUsers(nextUsers);
  }, []);

  useEffect(() => {
    request<User>("/api/v1/auth/me")
      .then(async (currentUser) => {
        setUser(currentUser);
        await Promise.all([loadProjects(), loadTemplates(), loadSnippets()]);
      })
      .catch(() => undefined);
  }, [loadProjects, loadSnippets, loadTemplates]);

  useEffect(() => {
    if (!user || nav !== "templates") return;
    void loadTemplates();
    const timer = window.setInterval(() => void loadTemplates(), 5000);
    return () => window.clearInterval(timer);
  }, [loadTemplates, nav, user]);

  useEffect(() => {
    if (!user || nav !== "admin" || user.role !== "super_admin") return;
    void loadAdminData().catch(() => setNotice("无法载入平台管理数据。"));
  }, [loadAdminData, nav, user]);

  const activeProject = projects.find((project) => project.id === activeProjectId) ?? null;
  const activeJobs = activeProjectId ? jobsByProject[activeProjectId] ?? [] : [];
  const activeJob = activeJobs.find((job) => job.id === selectedJobId) ?? activeJobs[0] ?? null;
  const workingJob = activeJobs.find((job) => job.status === "queued" || job.status === "running") ?? null;
  const refinementBaseJob = activeJob?.target_slide_number && !activeJob.base_job_id
    ? activeJobs.find((job) => job.status === "succeeded" && !job.target_slide_number) ?? null
    : null;
  const previewSlides = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "svg").sort((left, right) => left.filename.localeCompare(right.filename, undefined, { numeric: true })),
    [artifacts],
  );
  const latestPptx = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "pptx").sort((left, right) => right.created_at.localeCompare(left.created_at))[0] ?? null,
    [artifacts],
  );
  const readyTemplates = templates.filter((template) => template.status === "ready" && template.is_active);
  const isPlatformAdmin = user?.role === "super_admin";
  const globalMatches = useMemo(() => {
    const query = globalQuery.trim().toLowerCase();
    if (!query) return [];
    return [
      ...projects.filter((project) => project.title.toLowerCase().includes(query)).map((project) => ({ type: "项目", id: project.id, label: project.title })),
      ...templates.filter((template) => template.name.toLowerCase().includes(query)).map((template) => ({ type: "模板", id: template.id, label: template.name })),
      ...snippets.filter((snippet) => `${snippet.name}${snippet.content}`.toLowerCase().includes(query)).map((snippet) => ({ type: "提示词", id: snippet.id, label: snippet.name })),
    ].slice(0, 8);
  }, [globalQuery, projects, snippets, templates]);

  useEffect(() => {
    if (!activeProject || !activeJob) {
      setArtifacts([]);
      setEvents([]);
      return;
    }
    let disposed = false;
    const prefix = `/api/v1/projects/${activeProject.id}/jobs/${activeJob.id}`;
    void Promise.all([request<Artifact[]>(`${prefix}/artifacts`), request<JobEvent[]>(`${prefix}/events`)])
      .then(([nextArtifacts, nextEvents]) => {
        if (!disposed) {
          setArtifacts(nextArtifacts);
          setEvents(nextEvents);
        }
      })
      .catch(() => undefined);
    const stream = new EventSource(`${prefix}/events/stream`);
    stream.addEventListener("job-event", (raw) => {
      const incoming = JSON.parse((raw as MessageEvent).data) as JobEvent;
      setEvents((current) => current.some((event) => event.id === incoming.id) ? current : [...current, incoming]);
      if (incoming.event_type === "status" || incoming.event_type === "artifact") {
        void request<Artifact[]>(`${prefix}/artifacts`).then(setArtifacts).catch(() => undefined);
        void request<Job[]>(`/api/v1/projects/${activeProject.id}/jobs`).then((jobs) => {
          setJobsByProject((current) => ({ ...current, [activeProject.id]: jobs }));
        }).catch(() => undefined);
      }
    });
    stream.addEventListener("complete", () => stream.close());
    return () => {
      disposed = true;
      stream.close();
    };
  }, [activeJob?.id, activeProject?.id]);

  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    try {
      if (token) {
        await request<User>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ token, username, display_name: displayName, password }) });
        setNotice("账号已创建，请使用新账号登录。");
        return;
      }
      setUser(await request<User>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }));
      await Promise.all([loadProjects(), loadTemplates(), loadSnippets()]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "登录失败，请稍后重试。");
    }
  }

  async function createProject(title: string): Promise<Project> {
    const project = await request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ title }) });
    setProjects((current) => [project, ...current]);
    setJobsByProject((current) => ({ ...current, [project.id]: [] }));
    setMaterialsByProject((current) => ({ ...current, [project.id]: [] }));
    return project;
  }

  async function uploadMaterialFiles(projectId: string, files: File[]): Promise<void> {
    if (!files.length) return;
    setMaterialUploading(true);
    try {
      const uploaded: ProjectMaterial[] = [];
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        const response = await fetch(`/api/v1/projects/${projectId}/materials`, { method: "POST", credentials: "include", body: form });
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `${file.name} 上传失败。`);
        uploaded.push(await response.json() as ProjectMaterial);
      }
      setMaterialsByProject((current) => ({ ...current, [projectId]: [...(current[projectId] ?? []), ...uploaded] }));
      setNotice(`已添加 ${uploaded.length} 份材料，生成时会自动读取。`);
    } finally {
      setMaterialUploading(false);
    }
  }

  async function deleteMaterial(projectId: string, materialId: string): Promise<void> {
    await request<void>(`/api/v1/projects/${projectId}/materials/${materialId}`, { method: "DELETE" });
    setMaterialsByProject((current) => ({ ...current, [projectId]: (current[projectId] ?? []).filter((item) => item.id !== materialId) }));
  }

  function startDraft(seed = "") {
    setDraft(seed);
    setSelectedTemplateId(null);
    setActiveProjectId(null);
    setSelectedJobId(null);
    setNotice("");
    setPendingMaterialFiles([]);
    setNav("projects");
  }

  async function openProject(project: Project) {
    setActiveProjectId(project.id);
    setSelectedJobId(null);
    setDraft((jobsByProject[project.id]?.[0]?.prompt) || project.title);
    setSelectedTemplateId(null);
    setNav("projects");
    setNotice("");
    void loadMaterials(project.id).catch(() => setNotice("无法载入项目材料。"));
  }

  async function startGeneration(promptOverride?: string, templateOverride?: string | null, baseJobId?: string | null, targetSlideNumber?: number | null, conversationMessage?: string, clientMessageId?: string): Promise<Job | null> {
    const content = (promptOverride ?? draft).trim();
    if (!content || isSubmitting || workingJob) return null;
    setIsSubmitting(true);
    setNotice("");
    try {
      let project = activeProject;
      if (!project) project = await createProject(content.slice(0, 48));
      const job = await request<Job>(`/api/v1/projects/${project.id}/jobs`, {
        method: "POST",
        body: JSON.stringify({ prompt: content, template_id: activeJobs.length || baseJobId ? null : (templateOverride ?? selectedTemplateId), base_job_id: baseJobId || null, target_slide_number: targetSlideNumber || null, conversation_message: conversationMessage || null, client_message_id: clientMessageId || null }),
      });
      setJobsByProject((current) => ({ ...current, [project!.id]: [job, ...(current[project!.id] ?? [])] }));
      setActiveProjectId(project.id);
      setSelectedJobId(job.id);
      setShowLogs(true);
      await loadProjects();
      return job;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建生成任务失败。");
      return null;
    } finally {
      setIsSubmitting(false);
    }
  }

  async function beginWorkspace() {
    const content = draft.trim();
    if (!content || isSubmitting) return;
    setIsSubmitting(true);
    setNotice("");
    try {
      const project = await createProject(content.slice(0, 48));
      await uploadMaterialFiles(project.id, pendingMaterialFiles);
      setPendingMaterialFiles([]);
      setActiveProjectId(project.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建项目失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function cancelJob() {
    if (!activeProject || !workingJob) return;
    try {
      const next = await request<Job>(`/api/v1/projects/${activeProject.id}/jobs/${workingJob.id}/cancel`, { method: "POST" });
      setJobsByProject((current) => ({ ...current, [activeProject.id]: (current[activeProject.id] ?? []).map((job) => job.id === next.id ? next : job) }));
      setNotice(next.status === "cancelled" ? "任务已中止。" : "正在中止当前任务…");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "中止任务失败。");
    }
  }

  function returnToBaseJob(baseJobId: string): void {
    const baseJob = activeJobs.find((job) => job.id === baseJobId);
    if (!baseJob) {
      setNotice("原演示文稿暂时不可用，请刷新项目后重试。" );
      return;
    }
    setSelectedJobId(baseJob.id);
    setPreviewIndex(0);
    setShowLogs(false);
    setNotice("");
  }

  async function useSnippet(snippet: PromptSnippet) {
    setDraft((current) => current.trim() ? `${current.trim()}\n\n${snippet.content}` : snippet.content);
    setNav("projects");
    await request<PromptSnippet>(`/api/v1/prompt-snippets/${snippet.id}/use`, { method: "POST" })
      .then((next) => setSnippets((current) => current.map((item) => item.id === next.id ? next : item)))
      .catch(() => undefined);
  }

  async function removeProject() {
    if (!targetProject) return;
    try {
      await request<void>(`/api/v1/projects/${targetProject.id}`, { method: "DELETE" });
      setProjects((current) => current.filter((project) => project.id !== targetProject.id));
      setJobsByProject((current) => {
        const next = { ...current };
        delete next[targetProject.id];
        return next;
      });
      if (activeProjectId === targetProject.id) startDraft();
      setModal(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除项目失败。");
      setModal(null);
    }
  }

  async function uploadTemplate(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setTemplateUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/v1/templates/import", { method: "POST", credentials: "include", body: form });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "模板上传失败。");
      const template = await response.json() as Template;
      setTemplates((current) => [template, ...current]);
      setNotice("模板已上传，正在提取版式和主题信息。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "模板上传失败。");
    } finally {
      setTemplateUploading(false);
    }
  }

  async function saveTemplateName() {
    if (!targetTemplate || !editorName.trim()) return;
    try {
      const next = await request<Template>(`/api/v1/templates/${targetTemplate.id}`, { method: "PATCH", body: JSON.stringify({ name: editorName.trim() }) });
      setTemplates((current) => current.map((template) => template.id === next.id ? next : template));
      setModal(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "模板重命名失败。");
    }
  }

  async function deleteTemplate(template: Template) {
    try {
      await request<void>(`/api/v1/templates/${template.id}`, { method: "DELETE" });
      setTemplates((current) => current.filter((item) => item.id !== template.id));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除模板失败。");
    }
  }

  async function saveSnippet() {
    if (!editorName.trim() || !editorContent.trim()) return;
    try {
      const payload = { name: editorName.trim(), content: editorContent.trim(), category: editorCategory.trim() || "个人" };
      const next = targetSnippet
        ? await request<PromptSnippet>(`/api/v1/prompt-snippets/${targetSnippet.id}`, { method: "PATCH", body: JSON.stringify(payload) })
        : await request<PromptSnippet>("/api/v1/prompt-snippets", { method: "POST", body: JSON.stringify(payload) });
      setSnippets((current) => targetSnippet ? current.map((item) => item.id === next.id ? next : item) : [next, ...current]);
      setModal(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存提示词失败。");
    }
  }

  async function deleteSnippet() {
    if (!targetSnippet) return;
    try {
      await request<void>(`/api/v1/prompt-snippets/${targetSnippet.id}`, { method: "DELETE" });
      setSnippets((current) => current.filter((item) => item.id !== targetSnippet.id));
      setModal(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除提示词失败。");
    }
  }

  async function logout() {
    await request<void>("/api/v1/auth/logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
  }

  if (editorRoute) return <PresentationEditor projectId={editorRoute[1]} jobId={editorRoute[2]} />;

  if (!user) {
    return <main className="zc-auth"><section className="zc-auth-card"><div className="zc-logo"><img src="/logo-128.png" alt="" /><span>智创PPT专家</span></div><h1>{token ? "创建工作区账号" : "登录智创PPT专家"}</h1><p>从想法到可编辑演示文稿</p><form onSubmit={submitAuth}><label>账号<input value={username} onChange={(event) => setUsername(event.target.value)} required autoComplete="username" /></label>{token && <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>}<label>密码<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" minLength={8} required autoComplete={token ? "new-password" : "current-password"} /></label><button className="zc-primary" type="submit">{token ? <UserPlus size={17} /> : <LogIn size={17} />}{token ? "完成注册" : "登录"}</button></form>{notice && <p className="zc-notice">{notice}</p>}</section></main>;
  }

  if (Boolean(activeProject)) {
    return <CreativeWorkspace project={activeProject!} job={activeJob} workingJob={workingJob} templates={readyTemplates} snippets={snippets} artifacts={artifacts} events={events} materials={materialsByProject[activeProject!.id] ?? []} materialUploading={materialUploading} onUploadMaterials={(files) => void uploadMaterialFiles(activeProject!.id, files)} onDeleteMaterial={(materialId) => void deleteMaterial(activeProject!.id, materialId)} initialTemplateId={selectedTemplateId} baseJobId={refinementBaseJob?.id ?? null} onBack={() => startDraft()} onGenerate={(prompt, templateId, baseJobId, targetSlideNumber, conversationMessage, clientMessageId) => startGeneration(prompt, templateId, baseJobId, targetSlideNumber, conversationMessage, clientMessageId)} onCancel={() => void cancelJob()} onReturnToBase={returnToBaseJob} />;
  }

  const replicaPageBody = nav === "templates"
    ? <TemplatesPage templates={templates} query={templateQuery} setQuery={setTemplateQuery} uploading={templateUploading} notice={notice} onUpload={uploadTemplate} onUse={(template) => { startDraft(); setSelectedTemplateId(template.id); }} onRename={(template) => { setTargetTemplate(template); setEditorName(template.name); setModal("template-rename"); }} onDelete={deleteTemplate} onRetry={async (template) => { const next = await request<Template>(`/api/v1/templates/${template.id}/retry`, { method: "POST" }); setTemplates((current) => current.map((item) => item.id === next.id ? next : item)); }} />
    : nav === "prompts"
      ? <PromptsPage snippets={snippets} query={promptQuery} setQuery={setPromptQuery} notice={notice} onUse={useSnippet} onCreate={() => { setTargetSnippet(null); setEditorName(""); setEditorContent(""); setEditorCategory("个人"); setModal("prompt-editor"); }} onEdit={(snippet) => { setTargetSnippet(snippet); setEditorName(snippet.name); setEditorContent(snippet.content); setEditorCategory(snippet.category); setModal("prompt-editor"); }} onDelete={(snippet) => { setTargetSnippet(snippet); setModal("prompt-delete"); }} />
      : nav === "admin" && isPlatformAdmin
        ? <AdminPage templates={adminTemplates} snippets={adminSnippets} users={adminUsers} onRefresh={() => void loadAdminData()} />
        : null;

  if (window.location.pathname === "/" && !activeProject) {
    return <ReplicaDashboardShell
      user={user}
      activeNav={nav}
      pageBody={replicaPageBody}
      projects={projects}
      jobsByProject={jobsByProject}
      templates={readyTemplates}
      snippets={snippets}
      pendingMaterialFiles={pendingMaterialFiles}
      onSelectMaterials={(files) => setPendingMaterialFiles((current) => [...current, ...files])}
      onRemovePendingMaterial={(index) => setPendingMaterialFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
      materialUploading={materialUploading}
      draft={draft}
      setDraft={setDraft}
      selectedTemplateId={selectedTemplateId}
      setSelectedTemplateId={setSelectedTemplateId}
      isSubmitting={isSubmitting}
      darkMode={darkMode}
      setDarkMode={setDarkMode}
      searchOpen={searchOpen}
      setSearchOpen={setSearchOpen}
      globalQuery={globalQuery}
      setGlobalQuery={setGlobalQuery}
      globalMatches={globalMatches}
      sidebarCollapsed={sidebarCollapsed}
      setSidebarCollapsed={setSidebarCollapsed}
      sidebarOpen={sidebarOpen}
      setSidebarOpen={setSidebarOpen}
      isPlatformAdmin={isPlatformAdmin}
      onNavigate={(next) => { setNav(next); setNotice(""); setSidebarOpen(false); }}
      onCreate={() => startDraft()}
      onBegin={() => void beginWorkspace()}
      onOpen={openProject}
      onDelete={(project) => { setTargetProject(project); setModal("project-delete"); }}
      targetProject={targetProject}
      deleteOpen={modal === "project-delete"}
      onCloseDelete={() => setModal(null)}
      onConfirmDelete={() => void removeProject()}
      onUseSnippet={useSnippet}
      onNotice={setNotice}
      notice={notice}
      onLogout={() => void logout()}
    />;
  }

  const content = activeProject ? <ProjectWorkbench project={activeProject} job={activeJob} workingJob={workingJob} draft={draft} setDraft={setDraft} templates={readyTemplates} selectedTemplateId={selectedTemplateId} setSelectedTemplateId={setSelectedTemplateId} artifacts={artifacts} events={events} showLogs={showLogs} setShowLogs={setShowLogs} previewSlides={previewSlides} latestPptx={latestPptx} previewIndex={previewIndex} setPreviewIndex={setPreviewIndex} isSubmitting={isSubmitting} notice={notice} onBack={() => startDraft()} onGenerate={() => void startGeneration()} onCancel={() => void cancelJob()} onUseSnippet={useSnippet} snippets={snippets} /> : nav === "projects" ? <ProjectsPage projects={projects} jobsByProject={jobsByProject} query={projectQuery} setQuery={setProjectQuery} view={projectView} setView={setProjectView} draft={draft} setDraft={setDraft} templates={readyTemplates} selectedTemplateId={selectedTemplateId} setSelectedTemplateId={setSelectedTemplateId} snippets={snippets} isSubmitting={isSubmitting} notice={notice} onCreate={() => void beginWorkspace()} onOpen={openProject} onDelete={(project) => { setTargetProject(project); setModal("project-delete"); }} onUseSnippet={useSnippet} /> : nav === "templates" ? <TemplatesPage templates={templates} query={templateQuery} setQuery={setTemplateQuery} uploading={templateUploading} notice={notice} onUpload={uploadTemplate} onUse={(template) => { startDraft(); setSelectedTemplateId(template.id); }} onRename={(template) => { setTargetTemplate(template); setEditorName(template.name); setModal("template-rename"); }} onDelete={deleteTemplate} onRetry={async (template) => { const next = await request<Template>(`/api/v1/templates/${template.id}/retry`, { method: "POST" }); setTemplates((current) => current.map((item) => item.id === next.id ? next : item)); }} /> : nav === "prompts" ? <PromptsPage snippets={snippets} query={promptQuery} setQuery={setPromptQuery} notice={notice} onUse={useSnippet} onCreate={() => { setTargetSnippet(null); setEditorName(""); setEditorContent(""); setEditorCategory("个人"); setModal("prompt-editor"); }} onEdit={(snippet) => { setTargetSnippet(snippet); setEditorName(snippet.name); setEditorContent(snippet.content); setEditorCategory(snippet.category); setModal("prompt-editor"); }} onDelete={(snippet) => { setTargetSnippet(snippet); setModal("prompt-delete"); }} /> : isPlatformAdmin ? <AdminPage templates={adminTemplates} snippets={adminSnippets} users={adminUsers} onRefresh={() => void loadAdminData()} /> : null;

  const pageTitle = activeProject ? activeProject.title : nav === "projects" ? "我的 PPT" : nav === "templates" ? "模板库" : nav === "prompts" ? "常用提示词" : "平台管理";
  const pageDescription = activeProject ? "创作、生成与精修" : nav === "projects" ? "创作与管理你的演示文稿" : nav === "templates" ? "管理你的个人演示模板" : nav === "prompts" ? "沉淀高频创作与修改指令" : "管理平台资源、用户与模型配置";
  return <div className={`zc-shell ${sidebarCollapsed ? "is-collapsed" : ""}`}><button className={`zc-scrim ${sidebarOpen ? "is-visible" : ""}`} aria-label="关闭导航" onClick={() => setSidebarOpen(false)} /><aside className={`zc-sidebar ${sidebarOpen ? "is-open" : ""}`}><div className="zc-brand"><img src="/logo-128.png" alt="" /><span>智创PPT专家</span></div><button className="zc-create" type="button" onClick={() => startDraft()}><Plus size={18} /><span>创建 PPT</span></button><nav><small>工作空间</small>{([ ["projects", "我的 PPT", FileStack], ["templates", "模板库", LayoutTemplate], ["prompts", "常用提示词", Zap] ] as const).map(([key, label, Icon]) => <button key={key} className={nav === key && !activeProject ? "is-active" : ""} type="button" onClick={() => { setNav(key); setActiveProjectId(null); setSidebarOpen(false); }}><Icon size={18} /><span>{label}</span>{key === "projects" && <i>{projects.length}</i>}</button>)}</nav><div className="zc-sidebar-bottom"><div className="zc-account"><span>{user.display_name.slice(0, 1)}</span><strong>{user.display_name}</strong>{isPlatformAdmin && <button type="button" onClick={() => { setNav("admin"); setActiveProjectId(null); }} title="平台设置" aria-label="平台设置"><Settings size={16} /></button>}<button type="button" onClick={() => void logout()} title="退出登录" aria-label="退出登录"><LogOut size={16} /></button></div></div></aside><main className="zc-main"><header className="zc-topbar"><button className="zc-icon zc-mobile-menu" type="button" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button><button className="zc-icon zc-collapse" type="button" aria-label={sidebarCollapsed ? "展开导航" : "收起导航"} onClick={() => setSidebarCollapsed((current) => !current)}>{sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}</button><div><strong>{pageTitle}</strong><span>{pageDescription}</span></div></header><div className="zc-content">{content}</div></main>{modal && <ModalFrame onClose={() => setModal(null)}>{modal === "project-delete" && <><h2>删除这个项目？</h2><p>项目中的全部生成记录和演示文稿将被删除，且无法恢复。</p><div className="zc-modal-actions"><button className="zc-secondary" onClick={() => setModal(null)}>取消</button><button className="zc-danger" onClick={() => void removeProject()}><Trash2 size={15} />确认删除</button></div></>}{modal === "template-rename" && <><h2>重命名模板</h2><label>模板名称<input value={editorName} onChange={(event) => setEditorName(event.target.value)} autoFocus /></label><div className="zc-modal-actions"><button className="zc-secondary" onClick={() => setModal(null)}>取消</button><button className="zc-primary" onClick={() => void saveTemplateName()}>保存</button></div></>}{modal === "prompt-editor" && <><h2>{targetSnippet ? "编辑提示词" : "新建提示词"}</h2><label>名称<input value={editorName} onChange={(event) => setEditorName(event.target.value)} autoFocus /></label><label>分类<input value={editorCategory} onChange={(event) => setEditorCategory(event.target.value)} /></label><label>提示词正文<textarea value={editorContent} onChange={(event) => setEditorContent(event.target.value)} rows={7} /></label><div className="zc-modal-actions"><button className="zc-secondary" onClick={() => setModal(null)}>取消</button><button className="zc-primary" onClick={() => void saveSnippet()} disabled={!editorName.trim() || !editorContent.trim()}>保存提示词</button></div></>}{modal === "prompt-delete" && <><h2>删除这个提示词？</h2><p>“{targetSnippet?.name}”将不再能用于新的创作和精修。</p><div className="zc-modal-actions"><button className="zc-secondary" onClick={() => setModal(null)}>取消</button><button className="zc-danger" onClick={() => void deleteSnippet()}><Trash2 size={15} />确认删除</button></div></>}</ModalFrame>}</div>;
}

type ReplicaDashboardShellProps = {
  user: User;
  activeNav: NavKey;
  pageBody: ReactNode;
  projects: Project[];
  jobsByProject: Record<string, Job[]>;
  templates: Template[];
  snippets: PromptSnippet[];
  pendingMaterialFiles: File[];
  onSelectMaterials: (files: File[]) => void;
  onRemovePendingMaterial: (index: number) => void;
  materialUploading: boolean;
  draft: string;
  setDraft: Dispatch<SetStateAction<string>>;
  selectedTemplateId: string | null;
  setSelectedTemplateId: Dispatch<SetStateAction<string | null>>;
  isSubmitting: boolean;
  darkMode: boolean;
  setDarkMode: Dispatch<SetStateAction<boolean>>;
  searchOpen: boolean;
  setSearchOpen: Dispatch<SetStateAction<boolean>>;
  globalQuery: string;
  setGlobalQuery: Dispatch<SetStateAction<string>>;
  globalMatches: Array<{ type: string; id: string; label: string }>;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: Dispatch<SetStateAction<boolean>>;
  sidebarOpen: boolean;
  setSidebarOpen: Dispatch<SetStateAction<boolean>>;
  isPlatformAdmin: boolean;
  onNavigate: (key: NavKey) => void;
  onCreate: () => void;
  onBegin: () => void;
  onOpen: (project: Project) => void;
  onDelete: (project: Project) => void;
  targetProject: Project | null;
  deleteOpen: boolean;
  onCloseDelete: () => void;
  onConfirmDelete: () => void;
  onUseSnippet: (snippet: PromptSnippet) => Promise<void>;
  onNotice: (message: string) => void;
  notice: string;
  onLogout: () => void;
};

function ReplicaDashboardShell(props: ReplicaDashboardShellProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"全部" | "进行中" | "已完成">("全部");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const visibleProjects = props.projects.filter((project) => {
    const job = props.jobsByProject[project.id]?.[0];
    const matchesQuery = project.title.toLowerCase().includes(query.trim().toLowerCase());
    const matchesFilter = filter === "全部" || (filter === "进行中" ? job?.status === "queued" || job?.status === "running" : job?.status === "succeeded");
    return matchesQuery && matchesFilter;
  });
  const selectedTemplate = props.templates.find((template) => template.id === props.selectedTemplateId);
  const quickStarts = ["季度经营分析", "产品发布方案", "行业解决方案"];

  return <div className={`zc-shell kppt-shell ${props.sidebarCollapsed ? "is-collapsed" : ""} ${props.darkMode ? "kppt-dark" : ""}`}>
    <button className={`zc-scrim ${props.sidebarOpen ? "is-visible" : ""}`} aria-label="关闭导航" onClick={() => props.setSidebarOpen(false)} />
    <aside className={`zc-sidebar kppt-sidebar ${props.sidebarOpen ? "is-open" : ""}`}>
      <div className="kppt-brand-row">
        <button className="kppt-brand" type="button" onClick={props.onCreate} aria-label="返回我的 PPT">
          <img src="/logo-128.png" alt="" />
          <span><strong>智创PPT专家</strong><small>AI PRESENTATION AGENT</small></span>
        </button>
      </div>
      <button className="kppt-create" type="button" onClick={props.onCreate}><Plus size={18} /><span>创建 PPT</span></button>
      <nav className="kppt-primary-nav" aria-label="工作空间">
        <small>工作空间</small>
        {([ ["projects", "我的 PPT", FileStack], ["templates", "模板库", LayoutTemplate], ["prompts", "常用提示词", Zap] ] as const).map(([key, label, Icon]) => <button className={props.activeNav === key ? "is-active" : ""} key={key} type="button" onClick={() => props.onNavigate(key)}><Icon size={18} /><span>{label}</span>{key === "projects" && <i>{props.projects.length}</i>}</button>)}
      </nav>
      <div className="kppt-sidebar-spacer" />
      <nav className="kppt-secondary-nav" aria-label="辅助导航">
        <button type="button" onClick={() => props.isPlatformAdmin ? props.onNavigate("admin") : props.onNotice("个人设置将在后续版本接入。") }><Settings size={17} /><span>设置</span></button>
      </nav>
      <div className="kppt-account">
        <span>{props.user.display_name.slice(0, 1)}</span>
        <div><strong>{props.user.display_name}</strong><small>{props.isPlatformAdmin ? "平台超级管理员" : "工作区成员"}</small></div>
        <button type="button" onClick={props.onLogout} title="退出登录" aria-label="退出登录"><LogOut size={15} /></button>
      </div>
    </aside>
    <main className="zc-main kppt-main">
      <header className="kppt-topbar">
        <div className="kppt-topbar-title"><button className="zc-icon zc-mobile-menu" type="button" aria-label="打开导航" onClick={() => props.setSidebarOpen(true)}><Menu size={19} /></button><button className="kppt-collapse" type="button" aria-label={props.sidebarCollapsed ? "展开导航" : "收起导航"} onClick={() => props.setSidebarCollapsed((current) => !current)}>{props.sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}</button><div><strong>{props.activeNav === "projects" ? "我的 PPT" : props.activeNav === "templates" ? "模板库" : props.activeNav === "prompts" ? "常用提示词" : "平台管理"}</strong><span>{props.activeNav === "projects" ? "创作与管理你的演示文稿" : props.activeNav === "templates" ? "管理你的个人演示模板" : props.activeNav === "prompts" ? "沉淀高频创作与修改指令" : "管理平台资源、用户与系统资产"}</span></div></div>
        <div className="kppt-topbar-actions"><button className="kppt-global-search" type="button" onClick={() => props.setSearchOpen(true)}><Search size={16} /><span>搜索项目、模板和提示词</span><kbd>⌘ K</kbd></button><button className="kppt-topbar-icon" type="button" aria-label={props.darkMode ? "切换浅色主题" : "切换深色主题"} onClick={() => props.setDarkMode((current) => !current)}><Moon size={17} /></button></div>
      </header>
      <div className="zc-content kppt-content">
        {props.activeNav !== "projects" ? <div className="kppt-page-body">{props.pageBody}</div> : <>
        <section className="kppt-dashboard-hero" aria-labelledby="kppt-create-title">
          <div className="kppt-eyebrow"><Sparkles size={14} />智创PPT专家</div>
          <h1 id="kppt-create-title">把一个想法，变成一套能讲清楚的 PPT</h1>
          <p>先梳理需求，再设计大纲；每一步都由你确认，生成后还能逐页对话精修。</p>
          <div className="kppt-composer"><textarea value={props.draft} onChange={(event) => props.setDraft(event.target.value)} placeholder="描述你想做的 PPT，例如：为集团管理层准备一份 15 页的云业务季度经营汇报……" rows={4} /><div className="kppt-composer-actions"><div><label className="kppt-soft-button"><Paperclip size={16} />{props.materialUploading ? "正在上传…" : "添加材料"}<input type="file" multiple accept=".pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt,.md,.markdown,.png,.jpg,.jpeg,.webp" onChange={(event) => { props.onSelectMaterials(Array.from(event.target.files || [])); event.target.value = ""; }} disabled={props.materialUploading} /></label><button className="kppt-tool-button kppt-template-button" type="button" aria-label="添加模板" title="添加模板" onClick={() => setToolsOpen((current) => !current)}><LayoutTemplate size={16} /><span>添加模板</span></button></div><button className="kppt-send" type="button" aria-label="开始创建 PPT" disabled={!props.draft.trim() || props.isSubmitting} onClick={props.onBegin}><Send size={17} /></button></div>{props.pendingMaterialFiles.length > 0 && <div className="kppt-material-chips" aria-label="待上传材料">{props.pendingMaterialFiles.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" aria-label={`移除 ${file.name}`} onClick={() => props.onRemovePendingMaterial(index)}><X size={12} /></button></span>)}</div>}{toolsOpen && <div className="kppt-tools-popover">{props.templates.length > 0 ? <><button type="button" className={!props.selectedTemplateId ? "is-selected" : ""} onClick={() => { props.setSelectedTemplateId(null); setToolsOpen(false); }}>自由创作</button>{props.templates.map((template) => <button key={template.id} type="button" className={props.selectedTemplateId === template.id ? "is-selected" : ""} onClick={() => { props.setSelectedTemplateId(template.id); setToolsOpen(false); }}>{template.name}<small>{template.page_count ?? 0} 页模板</small></button>)}</> : <p className="kppt-tools-empty">暂无模板</p>}{props.snippets.map((snippet) => <button key={snippet.id} type="button" onClick={() => { void props.onUseSnippet(snippet); setToolsOpen(false); }}><span>引用提示词</span>{snippet.name}</button>)}</div>}</div>
          <div className="kppt-quick-starts"><span>试试：</span>{quickStarts.map((item) => <button key={item} type="button" onClick={() => props.setDraft(`帮我制作一份${item} PPT`)}>{item}</button>)}</div>
          {selectedTemplate && <p className="kppt-selected-template">已选择模板：{selectedTemplate.name}</p>}
          {props.notice && <p className="kppt-dashboard-notice">{props.notice}</p>}
        </section>
        <section className="kppt-project-section" aria-labelledby="kppt-recent-title">
          <header className="kppt-section-heading"><div><h2 id="kppt-recent-title">最近项目</h2><p>继续上次的创作，所有阶段都已自动保存</p></div><button className="kppt-text-button" type="button" onClick={() => { setFilter("全部"); setQuery(""); }}>查看全部<ArrowRight size={15} /></button></header>
          <div className="kppt-project-toolbar"><div className="kppt-segmented">{(["全部", "进行中", "已完成"] as const).map((item) => <button key={item} className={filter === item ? "is-active" : ""} type="button" onClick={() => setFilter(item)}>{item}</button>)}</div><div><label className="kppt-compact-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" /></label><button className={`kppt-tool-button ${filtersOpen ? "is-active" : ""}`} type="button" aria-label="筛选项目" onClick={() => setFiltersOpen((current) => !current)}><Filter size={16} /></button><button className="kppt-tool-button" type="button" aria-label="切换项目视图" onClick={() => setView((current) => current === "grid" ? "list" : "grid")}>{view === "grid" ? <List size={16} /> : <LayoutGrid size={16} />}</button></div></div>
          {filtersOpen && <div className="kppt-filter-strip"><span>项目范围</span><button type="button" className="is-active">全部项目</button><button type="button" onClick={() => props.onNotice("星标项目功能将在后续版本接入。")}>星标项目</button><small>按最近编辑排序</small></div>}
          <div className={`kppt-project-grid ${view === "list" ? "is-list" : ""}`}><button className="kppt-new-project" type="button" onClick={props.onCreate}><span><FilePlus2 size={24} /></span><strong>创建新 PPT</strong><small>从一句需求开始</small></button>{visibleProjects.map((project, index) => <ReplicaProjectCard key={project.id} project={project} job={props.jobsByProject[project.id]?.[0]} palette={index % 3} onOpen={() => props.onOpen(project)} onDelete={() => props.onDelete(project)} />)}</div>
        </section></>}
      </div>
    </main>
    {props.searchOpen && <div className="kppt-search-backdrop" onMouseDown={() => props.setSearchOpen(false)}><section className="kppt-search-dialog" role="dialog" aria-modal="true" aria-label="全局搜索" onMouseDown={(event) => event.stopPropagation()}><label><Search size={17} /><input autoFocus value={props.globalQuery} onChange={(event) => props.setGlobalQuery(event.target.value)} placeholder="搜索项目、模板和提示词" /></label><div>{props.globalMatches.length ? props.globalMatches.map((match) => <button key={`${match.type}-${match.id}`} type="button" onClick={() => { props.setSearchOpen(false); if (match.type === "项目") { const project = props.projects.find((item) => item.id === match.id); if (project) props.onOpen(project); } else if (match.type === "模板") { props.onNavigate("templates"); } else { const snippet = props.snippets.find((item) => item.id === match.id); if (snippet) void props.onUseSnippet(snippet); } }}><small>{match.type}</small><span>{match.label}</span></button>) : <p>{props.globalQuery ? "没有找到匹配内容" : "输入关键词，搜索项目、模板和提示词"}</p>}</div></section></div>}
    {props.deleteOpen && <div className="zc-modal-backdrop" onMouseDown={props.onCloseDelete}><section className="zc-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}><h2>删除这个项目？</h2><p>项目中的全部生成记录和演示文稿将被删除，且无法恢复。</p><div className="zc-modal-actions"><button className="zc-secondary" type="button" onClick={props.onCloseDelete}>取消</button><button className="zc-danger" type="button" onClick={props.onConfirmDelete}><Trash2 size={15} />确认删除</button></div></section></div>}
  </div>;
}

function ReplicaProjectCard({ project, job, palette, onOpen, onDelete }: { project: Project; job?: Job; palette: number; onOpen: () => void; onDelete: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const status = job ? statusLabel[job.status] : "草稿";
  return <article className={`kppt-project-card kppt-palette-${palette}`}><button className="kppt-project-preview" type="button" onClick={onOpen}><span>AI · DECK</span><div><strong>{project.title}</strong><small>{job?.template_name || "需求确认后可继续生成"}</small></div><i /><i /></button><div className="kppt-project-body"><button type="button" onClick={onOpen}>{project.title}</button><button className="kppt-card-menu" type="button" aria-label="项目更多操作" onClick={() => setMenuOpen((current) => !current)}><MoreHorizontal size={17} /></button>{menuOpen && <div className="kppt-card-menu-popover"><button type="button" onClick={onOpen}><Pencil size={14} />继续创作</button><button className="is-danger" type="button" onClick={onDelete}><Trash2 size={14} />删除项目</button></div>}</div><footer><span className={`kppt-status kppt-status-${job?.status ?? "draft"}`}>{status}</span><small>{formatDate(project.updated_at)}</small></footer></article>;
}

function ProjectsPage(props: { projects: Project[]; jobsByProject: Record<string, Job[]>; query: string; setQuery: (value: string) => void; view: "grid" | "list"; setView: (value: "grid" | "list") => void; draft: string; setDraft: (value: string) => void; templates: Template[]; selectedTemplateId: string | null; setSelectedTemplateId: (value: string | null) => void; snippets: PromptSnippet[]; isSubmitting: boolean; notice: string; onCreate: () => void; onOpen: (project: Project) => void; onDelete: (project: Project) => void; onUseSnippet: (snippet: PromptSnippet) => void }) {
  const [showTemplates, setShowTemplates] = useState(false);
  const [showPrompts, setShowPrompts] = useState(false);
  const visible = props.projects.filter((project) => project.title.toLowerCase().includes(props.query.toLowerCase()));
  const selectedTemplate = props.templates.find((template) => template.id === props.selectedTemplateId);
  return <><section className="zc-hero"><div className="zc-eyebrow">AI 演示文稿创作</div><h1>把一个想法，变成一套能讲清楚的 PPT</h1><p>从需求到可编辑演示文稿，所有项目都在这里持续创作。</p><div className="zc-composer"><textarea value={props.draft} onChange={(event) => props.setDraft(event.target.value)} placeholder="例如：为人工智能行业发展准备一份 12 页的趋势分析 PPT，面向企业管理层" rows={5} /><div><div className="zc-composer-tools"><button className={showTemplates ? "is-active" : ""} type="button" onClick={() => setShowTemplates((current) => !current)}><LayoutTemplate size={16} />{selectedTemplate ? selectedTemplate.name : "选择模板"}</button><button className={showPrompts ? "is-active" : ""} type="button" onClick={() => setShowPrompts((current) => !current)}><Zap size={16} />引用提示词</button></div><button className="zc-send" type="button" aria-label="开始创作" disabled={!props.draft.trim() || props.isSubmitting} onClick={props.onCreate}>{props.isSubmitting ? <LoaderCircle className="zc-spin" size={18} /> : <Send size={18} />}</button></div>{showTemplates && <div className="zc-composer-popover"> <button className={!props.selectedTemplateId ? "is-selected" : ""} type="button" onClick={() => { props.setSelectedTemplateId(null); setShowTemplates(false); }}>自由创作</button>{props.templates.map((template) => <button className={props.selectedTemplateId === template.id ? "is-selected" : ""} type="button" key={template.id} onClick={() => { props.setSelectedTemplateId(template.id); setShowTemplates(false); }}>{template.name}<small>{template.page_count ?? 0} 页</small></button>)}</div>}{showPrompts && <div className="zc-composer-popover zc-prompt-popover">{props.snippets.length ? props.snippets.map((snippet) => <button type="button" key={snippet.id} onClick={() => { void props.onUseSnippet(snippet); setShowPrompts(false); }}><strong>{snippet.name}</strong><span>{snippet.content}</span></button>) : <p>还没有保存提示词</p>}</div>}</div>{props.notice && <p className="zc-inline-notice">{props.notice}</p>}</section><section className="zc-section"><header className="zc-section-head"><div><h2>最近项目</h2><p>继续上次的创作，生成过程和产物都会自动保存。</p></div><div className="zc-toolbar"><label><Search size={16} /><input value={props.query} onChange={(event) => props.setQuery(event.target.value)} placeholder="搜索项目" /></label><button className="zc-icon" type="button" aria-label="切换项目视图" onClick={() => props.setView(props.view === "grid" ? "list" : "grid")}>{props.view === "grid" ? <List size={17} /> : <LayoutGrid size={17} />}</button></div></header><div className={`zc-project-grid ${props.view === "list" ? "is-list" : ""}`}><button className="zc-new-project" type="button" onClick={() => document.querySelector<HTMLTextAreaElement>(".zc-composer textarea")?.focus()}><FilePlus2 size={25} /><strong>创建新 PPT</strong><span>从一句需求开始</span></button>{visible.map((project) => <ProjectCard key={project.id} project={project} job={props.jobsByProject[project.id]?.[0]} onOpen={() => props.onOpen(project)} onDelete={() => props.onDelete(project)} />)}</div></section></>;
}

function ProjectCard({ project, job, onOpen, onDelete }: { project: Project; job?: Job; onOpen: () => void; onDelete: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const status = job ? statusLabel[job.status] : "草稿";
  return <article className="zc-project-card"><button className="zc-project-cover" type="button" onClick={onOpen}><span>AI<br />PPT</span><strong>{project.title}</strong><i /></button><div className="zc-project-card-body"><div><button className="zc-project-title" type="button" onClick={onOpen}>{project.title}</button><small>{job?.template_name || "自由创作"} · 最近编辑 {formatDate(project.updated_at)}</small></div><button className="zc-icon zc-card-menu" type="button" aria-label="项目操作" onClick={() => setMenuOpen((current) => !current)}><MoreHorizontal size={18} /></button>{menuOpen && <div className="zc-action-menu"><button type="button" onClick={onOpen}><Pencil size={14} />继续创作</button><button className="is-danger" type="button" onClick={onDelete}><Trash2 size={14} />删除项目</button></div>}</div><footer><span className={`zc-status zc-status-${job?.status ?? "draft"}`}>{status}</span>{job?.template_name && <span>{job.template_name}</span>}</footer></article>;
}

function TemplatesPage(props: { templates: Template[]; query: string; setQuery: (value: string) => void; uploading: boolean; notice: string; onUpload: (event: ChangeEvent<HTMLInputElement>) => void; onUse: (template: Template) => void; onRename: (template: Template) => void; onDelete: (template: Template) => void; onRetry: (template: Template) => Promise<void> }) {
  const [tab, setTab] = useState<"system" | "mine">("system");
  const [category, setCategory] = useState("全部场景");
  const categories = Array.from(new Set(props.templates.map((template) => template.metadata.category ? String(template.metadata.category) : "商务汇报")));
  const visible = props.templates.filter((template) => {
    const matchesOwner = tab === "system" ? template.scope === "system" : template.scope !== "system";
    const matchesQuery = template.name.toLowerCase().includes(props.query.toLowerCase());
    const templateCategory = template.metadata.category ? String(template.metadata.category) : "商务汇报";
    return matchesOwner && matchesQuery && (category === "全部场景" || templateCategory === category);
  });
  const card = (template: Template) => <article className="zc-template-card" key={template.id}><button className="zc-template-preview" type="button" onClick={() => template.status === "ready" && template.is_active && props.onUse(template)}>{template.metadata.preview_files && Array.isArray(template.metadata.preview_files) && template.metadata.preview_files[0] ? <img src={`/api/v1/templates/${template.id}/files/${String(template.metadata.preview_files[0])}`} alt="" /> : <LayoutTemplate size={30} />}<span>{template.scope === "system" ? "系统模板" : template.status === "ready" ? "用于新建 PPT" : template.status === "analyzing" ? "正在分析" : "解析失败"}</span></button><div><strong>{template.name}</strong><small>{template.page_count ? `${template.page_count} 页模板` : "正在读取页面信息"}</small></div>{template.scope !== "system" && <footer>{template.status === "failed" ? <button type="button" onClick={() => void props.onRetry(template)}>重新分析</button> : <button type="button" onClick={() => props.onRename(template)}><Edit3 size={14} />重命名</button>}<button className="is-danger" type="button" onClick={() => props.onDelete(template)}><Trash2 size={14} />删除</button></footer>}{template.error && <p className="zc-template-error">{template.error}</p>}</article>;
  return <section className="zc-asset-page kppt-asset-page"><header className="zc-asset-head"><div><div className="zc-eyebrow">设计资产</div><h1>模板库</h1><p>选择平台模板，或上传团队已有的 PPTX 作为生成风格。</p></div><label className="zc-primary zc-upload"><Upload size={16} />{props.uploading ? "正在上传…" : "上传模板"}<input type="file" accept=".pptx" onChange={props.onUpload} disabled={props.uploading} /></label></header>{props.notice && <p className="zc-inline-notice">{props.notice}</p>}<div className="kppt-asset-tabs"><div className="kppt-tab-list" role="tablist"><button type="button" className={tab === "system" ? "is-active" : ""} onClick={() => setTab("system")}>系统模板 <span>{props.templates.filter((template) => template.scope === "system").length}</span></button><button type="button" className={tab === "mine" ? "is-active" : ""} onClick={() => setTab("mine")}>我的模板 <span>{props.templates.filter((template) => template.scope !== "system").length}</span></button></div><div className="kppt-asset-tools"><label className="kppt-compact-search"><Search size={15} /><input value={props.query} onChange={(event) => props.setQuery(event.target.value)} placeholder="搜索模板或标签" /></label><label className="kppt-category-select"><ListFilter size={15} /><select value={category} onChange={(event) => setCategory(event.target.value)}><option>全部场景</option>{categories.map((item) => <option key={item}>{item}</option>)}</select><ChevronDown size={14} /></label><button className="kppt-tool-button" type="button" aria-label="网格视图"><LayoutGrid size={16} /></button></div></div>{visible.length === 0 ? <div className="kppt-empty-state"><LayoutTemplate size={25} /><strong>还没有匹配的模板</strong><span>上传一个 PPTX，建立你的个人模板资产。</span></div> : <div className="zc-template-grid kppt-template-grid">{tab === "mine" && <label className="zc-template-upload kppt-upload-template"><Plus size={22} /><strong>上传新模板</strong><span>支持 .pptx，建议使用 16:9 页面</span><input type="file" accept=".pptx" onChange={props.onUpload} disabled={props.uploading} /></label>}{visible.map(card)}</div>}</section>;
}

function PromptsPage(props: { snippets: PromptSnippet[]; query: string; setQuery: (value: string) => void; notice: string; onUse: (snippet: PromptSnippet) => void; onCreate: () => void; onEdit: (snippet: PromptSnippet) => void; onDelete: (snippet: PromptSnippet) => void }) {
  const visible = props.snippets.filter((snippet) => `${snippet.name}${snippet.content}`.toLowerCase().includes(props.query.toLowerCase()));
  const renderCard = (snippet: PromptSnippet) => <article className="zc-prompt-card" key={snippet.id}><header><span><Zap size={15} /></span><small>{snippet.scope === "system" ? "系统" : snippet.category}</small><button type="button" onClick={() => void props.onUse(snippet)}><WandSparkles size={14} />引用</button></header><h3>{snippet.name}</h3><p>{snippet.content}</p><footer><span>已使用 {snippet.used_count} 次</span><div><button type="button" aria-label="复制提示词" onClick={() => void navigator.clipboard?.writeText(snippet.content)}><Copy size={15} /></button>{snippet.scope !== "system" && <><button type="button" aria-label="编辑提示词" onClick={() => props.onEdit(snippet)}><Edit3 size={15} /></button><button type="button" aria-label="删除提示词" onClick={() => props.onDelete(snippet)}><Trash2 size={15} /></button></>}</div></footer></article>;
  const systemSnippets = visible.filter((snippet) => snippet.scope === "system");
  const personalSnippets = visible.filter((snippet) => snippet.scope !== "system");
  return <section className="zc-asset-page kppt-asset-page prompt-page"><header className="zc-asset-head"><div><div className="zc-eyebrow">个人效率资产</div><h1>常用提示词</h1><p>沉淀高频修改指令，在创作工作区里随时引用。</p></div><button className="zc-primary" type="button" onClick={props.onCreate}><Plus size={16} />新建提示词</button></header>{props.notice && <p className="zc-inline-notice">{props.notice}</p>}<div className="zc-prompt-summary"><span><Sparkles size={20} /></span><div><strong>让每一次修改更快一步</strong><small>已保存 {personalSnippets.length} 条个人指令，本月累计引用 {props.snippets.reduce((total, item) => total + item.used_count, 0)} 次</small></div><label><Search size={16} /><input value={props.query} onChange={(event) => props.setQuery(event.target.value)} placeholder="搜索提示词" /></label></div>{systemSnippets.length > 0 && <><h2 className="zc-library-heading">系统提示词</h2><div className="zc-prompt-grid kppt-prompt-grid">{systemSnippets.map(renderCard)}</div></>}<h2 className="zc-library-heading">我的提示词</h2><div className="zc-prompt-grid kppt-prompt-grid">{personalSnippets.map(renderCard)}</div></section>;
}

function AdminPage({ templates, snippets, users, onRefresh }: { templates: Template[]; snippets: PromptSnippet[]; users: User[]; onRefresh: () => void }) {
  const [tab, setTab] = useState<"templates" | "prompts" | "users">("templates");
  const [notice, setNotice] = useState("");
  const [promptName, setPromptName] = useState("");
  const [promptCategory, setPromptCategory] = useState("平台");
  const [promptContent, setPromptContent] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true);
    setNotice("");
    try {
      await action();
      onRefresh();
      setNotice(success);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "操作失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function importTemplate(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await run(async () => {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/v1/admin/system-templates/import", { method: "POST", credentials: "include", body: form });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "系统模板上传失败。");
    }, "系统模板已加入解析队列。");
  }

  return <section className="zc-admin-page"><header className="zc-asset-head"><div><div className="zc-eyebrow">Platform Control</div><h1>平台管理</h1><p>维护所有用户可见的模板、提示词和工作区账号。</p></div><span className="zc-admin-badge"><ShieldCheck size={16} />超级管理员</span></header>{notice && <p className="zc-inline-notice">{notice}</p>}<div className="zc-admin-layout"><aside><button className={tab === "templates" ? "is-active" : ""} type="button" onClick={() => setTab("templates")}><LayoutTemplate size={17} />系统模板</button><button className={tab === "prompts" ? "is-active" : ""} type="button" onClick={() => setTab("prompts")}><Zap size={17} />系统提示词</button><button className={tab === "users" ? "is-active" : ""} type="button" onClick={() => setTab("users")}><Users size={17} />用户管理</button></aside><main>{tab === "templates" && <><div className="zc-admin-section-head"><div><h2>系统模板</h2><p>启用后，所有用户都可在创建时选择。</p></div><label className="zc-primary zc-upload"><Upload size={16} />上传系统模板<input type="file" accept=".pptx" onChange={importTemplate} disabled={busy} /></label></div><div className="zc-template-grid">{templates.map((template) => <article className="zc-template-card" key={template.id}><div className="zc-template-preview">{template.metadata.preview_files && Array.isArray(template.metadata.preview_files) && template.metadata.preview_files[0] ? <img src={`/api/v1/templates/${template.id}/files/${String(template.metadata.preview_files[0])}`} alt="" /> : <LayoutTemplate size={30} />}<span>{template.is_active ? "已启用" : "已停用"}</span></div><div><strong>{template.name}</strong><small>{template.page_count ? `${template.page_count} 页模板` : "正在读取页面信息"}</small></div><footer><button type="button" onClick={() => void run(() => request(`/api/v1/admin/system-templates/${template.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !template.is_active }) }).then(() => undefined), template.is_active ? "系统模板已停用。" : "系统模板已启用。")}>{template.is_active ? "停用" : "启用"}</button><button className="is-danger" type="button" onClick={() => void run(() => request<void>(`/api/v1/admin/system-templates/${template.id}`, { method: "DELETE" }), "系统模板已删除。")}><Trash2 size={14} />删除</button></footer></article>)}</div></>}{tab === "prompts" && <><div className="zc-admin-section-head"><div><h2>系统提示词</h2><p>系统提示词可被所有用户引用，但只能在此处维护。</p></div></div><form className="zc-admin-prompt-form" onSubmit={(event) => { event.preventDefault(); void run(async () => { await request("/api/v1/admin/system-prompts", { method: "POST", body: JSON.stringify({ name: promptName, category: promptCategory, content: promptContent }) }); setPromptName(""); setPromptContent(""); }, "系统提示词已创建。"); }}><input value={promptName} onChange={(event) => setPromptName(event.target.value)} placeholder="提示词名称" required /><input value={promptCategory} onChange={(event) => setPromptCategory(event.target.value)} placeholder="分类" required /><textarea value={promptContent} onChange={(event) => setPromptContent(event.target.value)} placeholder="提示词正文" rows={3} required /><button className="zc-primary" type="submit" disabled={busy}>发布系统提示词</button></form><div className="zc-prompt-grid">{snippets.map((snippet) => <article className="zc-prompt-card" key={snippet.id}><header><span><Zap size={15} /></span><small>{snippet.category}</small><button type="button" onClick={() => void run(() => request(`/api/v1/admin/system-prompts/${snippet.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !snippet.is_active }) }).then(() => undefined), snippet.is_active ? "系统提示词已停用。" : "系统提示词已启用。")}>{snippet.is_active ? "停用" : "启用"}</button></header><h3>{snippet.name}</h3><p>{snippet.content}</p><footer><span>已使用 {snippet.used_count} 次</span><div><button type="button" aria-label="删除系统提示词" onClick={() => void run(() => request<void>(`/api/v1/admin/system-prompts/${snippet.id}`, { method: "DELETE" }), "系统提示词已删除。")}><Trash2 size={15} /></button></div></footer></article>)}</div></>}{tab === "users" && <><div className="zc-admin-section-head"><div><h2>用户管理</h2><p>账号停用后将无法登录，正在执行的任务会请求中止。</p></div></div><div className="zc-user-table"><header><span>用户</span><span>账号</span><span>角色</span><span>状态</span><span>操作</span></header>{users.map((account) => <div key={account.id}><strong>{account.display_name}</strong><span>{account.username}</span><span>{account.role === "super_admin" ? "超级管理员" : account.role === "admin" ? "管理员" : "成员"}</span><span className={account.is_active ? "zc-user-active" : "zc-user-inactive"}>{account.is_active ? "启用" : "已停用"}</span><button className="zc-secondary" type="button" disabled={account.role === "super_admin" || busy} onClick={() => void run(() => request(`/api/v1/admin/users/${account.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !account.is_active }) }).then(() => undefined), account.is_active ? "用户已停用。" : "用户已启用。")}>{account.is_active ? "停用" : "启用"}</button></div>)}</div></>}</main></div></section>;
}

function ProjectWorkbench(props: { project: Project; job: Job | null; workingJob: Job | null; draft: string; setDraft: (value: string) => void; templates: Template[]; selectedTemplateId: string | null; setSelectedTemplateId: (value: string | null) => void; artifacts: Artifact[]; events: JobEvent[]; showLogs: boolean; setShowLogs: (value: boolean) => void; previewSlides: Artifact[]; latestPptx: Artifact | null; previewIndex: number | null; setPreviewIndex: (value: number | null) => void; isSubmitting: boolean; notice: string; onBack: () => void; onGenerate: () => void; onCancel: () => void; onUseSnippet: (snippet: PromptSnippet) => void; snippets: PromptSnippet[] }) {
  const job = props.job;
  const stageIndex: number = job?.status === "succeeded" ? 4 : props.workingJob ? 3 : props.selectedTemplateId ? 2 : 0;
  const projectJobRef = `${props.project.id}:${job?.id ?? ""}`;
  const previewArtifact = props.previewIndex === null ? null : props.previewSlides[props.previewIndex];
  return <section className="zc-workbench"><header className="zc-workbench-head"><button className="zc-icon" type="button" aria-label="返回项目" onClick={props.onBack}><ArrowLeft size={19} /></button><div><strong>{props.project.title}</strong><span>{job?.template_name ? `使用模板：${job.template_name}` : "自由创作"}</span></div><span className={`zc-status zc-status-${job?.status ?? "draft"}`}>{job ? statusLabel[job.status] : "需求草稿"}</span></header><div className="zc-stage-bar">{workbenchStages.map((stage, index) => <div className={index <= stageIndex ? "is-active" : ""} key={stage}><i>{index < stageIndex ? <Check size={13} /> : index + 1}</i><span>{stage}</span></div>)}</div>{stageIndex < 3 && <div className="zc-workbench-body"><aside><strong>创作流程</strong><button className={stageIndex === 0 ? "is-current" : ""} type="button">需求梳理</button><button className={stageIndex === 1 ? "is-current" : ""} type="button">大纲设计</button><button className={stageIndex === 2 ? "is-current" : ""} type="button">选择模板</button></aside><main><div className="zc-panel"><div className="zc-panel-head"><div><h2>{stageIndex === 2 ? "为这份演示文稿选择模板" : "梳理你的创作需求"}</h2><p>{stageIndex === 2 ? "模板会固定在本次生成任务中，后续可继续基于结果精修。" : "输入主题、对象、页数和表达风格。你可以随时回到这里补充信息。"}</p></div></div>{stageIndex === 2 ? <div className="zc-workbench-template-grid"><button className={!props.selectedTemplateId ? "is-selected" : ""} type="button" onClick={() => props.setSelectedTemplateId(null)}><LayoutTemplate size={20} /><strong>自由创作</strong><span>不使用个人模板</span></button>{props.templates.map((template) => <button className={props.selectedTemplateId === template.id ? "is-selected" : ""} type="button" key={template.id} onClick={() => props.setSelectedTemplateId(template.id)}><LayoutTemplate size={20} /><strong>{template.name}</strong><span>{template.page_count ?? 0} 页模板</span></button>)}</div> : <><label className="zc-brief-field"><span>演示需求</span><textarea value={props.draft} onChange={(event) => props.setDraft(event.target.value)} rows={9} placeholder="描述希望生成的 PPT" /></label><div className="zc-quick-prompts">{props.snippets.slice(0, 4).map((snippet) => <button type="button" key={snippet.id} onClick={() => void props.onUseSnippet(snippet)}><Zap size={14} />{snippet.name}</button>)}</div></>}<footer className="zc-panel-actions"><button className="zc-secondary" type="button" onClick={() => props.setSelectedTemplateId(props.selectedTemplateId || null)}>{stageIndex === 0 ? "继续完善大纲" : "返回修改需求"}</button><button className="zc-primary" type="button" onClick={props.onGenerate} disabled={!props.draft.trim() || props.isSubmitting}>{props.isSubmitting ? <LoaderCircle className="zc-spin" size={16} /> : <Sparkles size={16} />}{stageIndex === 2 ? "开始生成 PPT" : "确认并开始生成"}</button></footer></div></main></div>}{stageIndex === 3 && <div className="zc-generating"><span><LoaderCircle className="zc-spin" size={28} /></span><h2>{job?.status === "queued" ? "正在准备生成任务" : "正在生成演示文稿"}</h2><p>你可以离开当前页面，任务会继续在后台执行。</p><div className="zc-generation-steps"><span>准备工作区</span><span>应用视觉规范</span><span>逐页生成内容</span><span>质量检查与导出</span></div><button className="zc-danger" type="button" onClick={props.onCancel} disabled={job?.cancellation_requested}><Square size={15} />{job?.cancellation_requested ? "正在中止" : "中止任务"}</button>{props.notice && <p className="zc-inline-notice">{props.notice}</p>}<button className="zc-log-toggle" type="button" onClick={() => props.setShowLogs(!props.showLogs)}>{props.showLogs ? "收起执行记录" : "查看执行记录"}<ChevronDown size={16} /></button>{props.showLogs && <div className="zc-log-panel">{props.events.map((event) => <p key={event.id}>{String(event.payload.message || event.payload.text || event.payload.event || event.event_type)}</p>)}</div>}</div>}{stageIndex === 4 && <div className="zc-preview-workspace"><aside className="zc-slide-list"><strong>页面预览</strong>{props.previewSlides.map((slide, index) => <button className={props.previewIndex === index ? "is-active" : ""} type="button" key={slide.id} onClick={() => props.setPreviewIndex(index)}><img src={artifactUrl(projectJobRef, slide.id)} alt={`第 ${index + 1} 页`} /><span>{index + 1}</span></button>)}</aside><main className="zc-preview-main">{previewArtifact ? <img src={artifactUrl(projectJobRef, previewArtifact.id)} alt={`第 ${(props.previewIndex ?? 0) + 1} 页预览`} /> : <div className="zc-preview-placeholder"><FileStack size={35} /><strong>选择一页查看详情</strong></div>}</main><aside className="zc-preview-actions"><h2>预览精修</h2><p>可以继续描述希望修改的内容，系统会基于当前版本生成新的演示文稿。</p>{props.latestPptx && <a className="zc-primary" href={artifactUrl(projectJobRef, props.latestPptx.id)} download><Download size={16} />下载演示文稿</a>}<a className="zc-secondary" href={`/editor/${props.project.id}/${job?.id ?? ""}`}><Pencil size={16} />手动编辑</a><label><span>继续修改</span><textarea value={props.draft} onChange={(event) => props.setDraft(event.target.value)} rows={5} placeholder="例如：把封面改得更简洁" /></label><button className="zc-primary" type="button" onClick={props.onGenerate} disabled={!props.draft.trim() || props.isSubmitting}><Send size={16} />提交修改</button>{props.notice && <p className="zc-inline-notice">{props.notice}</p>}</aside></div>}</section>;
}

function ModalFrame({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return <div className="zc-modal-backdrop" role="presentation"><section className="zc-modal" role="dialog" aria-modal="true"><button className="zc-icon zc-modal-close" type="button" aria-label="关闭" onClick={onClose}><X size={18} /></button>{children}</section></div>;
}
