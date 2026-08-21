import { ChangeEvent, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  LoaderCircle,
  Presentation,
  RotateCcw,
  Save,
  Type,
} from "lucide-react";

type Slide = { id: string; filename: string; size_bytes: number };
type SelectedElement = {
  key: string;
  tag: string;
  text: string;
  attrs: Record<string, string>;
  canEditText: boolean;
};

type EditorProps = { projectId: string; jobId: string };
type EditorExportEvent = { event_type: string; payload: Record<string, unknown> };

const editableAttributes = ["fill", "font-size", "font-family", "x", "y", "width", "height", "opacity"];

function editorUrl(projectId: string, jobId: string, slideId?: string): string {
  const base = `/api/v1/projects/${projectId}/jobs/${jobId}/editor/slides`;
  return slideId ? `${base}/${slideId}` : base;
}

function prepareSvg(content: string): string {
  const document = new DOMParser().parseFromString(content, "image/svg+xml");
  const svg = document.documentElement;
  svg.querySelectorAll("*").forEach((element, index) => {
    if (["defs", "style", "title", "desc", "metadata"].includes(element.localName)) return;
    if (!element.hasAttribute("data-editor-key")) element.setAttribute("data-editor-key", `element-${index}`);
  });
  return new XMLSerializer().serializeToString(svg);
}

function readError(response: Response): Promise<string> {
  return response.json()
    .then((body) => String(body.detail || "请求失败，请稍后重试。"))
    .catch(() => "请求失败，请稍后重试。");
}

function clearEditorSelection(root: ParentNode): void {
  root.querySelectorAll(".ppt-editor-selected").forEach((item) => {
    item.classList.remove("ppt-editor-selected");
    if (!item.getAttribute("class")?.trim()) item.removeAttribute("class");
  });
  root.querySelectorAll('[class=""]').forEach((item) => item.removeAttribute("class"));
}

export function PresentationEditor({ projectId, jobId }: EditorProps) {
  const [slides, setSlides] = useState<Slide[]>([]);
  const [activeSlideId, setActiveSlideId] = useState<string | null>(null);
  const [sourceMarkup, setSourceMarkup] = useState("");
  const [selected, setSelected] = useState<SelectedElement | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [isDirty, setIsDirty] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [exportState, setExportState] = useState<"idle" | "queued" | "exporting" | "succeeded" | "failed">("idle");
  const [message, setMessage] = useState("");
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const activeIndex = slides.findIndex((slide) => slide.id === activeSlideId);
  const activeSlide = activeIndex >= 0 ? slides[activeIndex] : null;

  useEffect(() => {
    let cancelled = false;
    fetch(editorUrl(projectId, jobId), { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json() as Promise<Slide[]>;
      })
      .then((items) => {
        if (cancelled) return;
        setSlides(items);
        setActiveSlideId(items[0]?.id ?? null);
      })
      .catch((error) => !cancelled && setMessage(error instanceof Error ? error.message : "无法载入演示文稿"))
      .finally(() => !cancelled && setIsLoading(false));
    return () => { cancelled = true; };
  }, [jobId, projectId]);

  useEffect(() => {
    if (!activeSlideId) return;
    let cancelled = false;
    setIsLoading(true);
    fetch(editorUrl(projectId, jobId, activeSlideId), { credentials: "include" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.text();
      })
      .then((content) => {
        if (cancelled) return;
        setSourceMarkup(prepareSvg(content));
        setHistory([]);
        setSelected(null);
        setIsDirty(false);
        setMessage("");
      })
      .catch((error) => !cancelled && setMessage(error instanceof Error ? error.message : "无法载入页面"))
      .finally(() => !cancelled && setIsLoading(false));
    return () => { cancelled = true; };
  }, [activeSlideId, jobId, projectId]);

  useEffect(() => {
    if (!selected) return;
    const element = findElement(selected.key);
    if (canvasRef.current) clearEditorSelection(canvasRef.current);
    element?.classList.add("ppt-editor-selected");
  }, [sourceMarkup, selected?.key]);

  useEffect(() => {
    if (exportState !== "queued" && exportState !== "exporting") return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const response = await fetch(`/api/v1/projects/${projectId}/jobs/${jobId}/events`, { credentials: "include" });
        if (!response.ok) throw new Error(await readError(response));
        const events = await response.json() as EditorExportEvent[];
        const latest = [...events].reverse().find((event) => event.event_type === "editor_export");
        const nextState = String(latest?.payload.status || "");
        if (cancelled || !["queued", "exporting", "succeeded", "failed"].includes(nextState)) return;
        setExportState(nextState as "queued" | "exporting" | "succeeded" | "failed");
        if (nextState === "succeeded") setMessage("新版演示文稿已导出，可以返回工作台下载。");
        if (nextState === "failed") setMessage(String(latest?.payload.text || "新版演示文稿导出失败，请检查本页内容后重新保存。"));
      } catch (error) {
        if (!cancelled) setMessage(error instanceof Error ? error.message : "无法获取新版演示文稿导出状态。");
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1_500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [exportState, jobId, projectId]);

  function findElement(key: string): SVGElement | null {
    return canvasRef.current?.querySelector(`svg [data-editor-key="${CSS.escape(key)}"]`) ?? null;
  }

  function selectElement(element: SVGElement | null) {
    if (canvasRef.current) clearEditorSelection(canvasRef.current);
    if (!element) {
      setSelected(null);
      return;
    }
    element.classList.add("ppt-editor-selected");
    const key = element.getAttribute("data-editor-key") || "";
    const tag = element.localName;
    const attrs = Object.fromEntries(editableAttributes.flatMap((name) => {
      const value = element.getAttribute(name);
      return value === null ? [] : [[name, value]];
    }));
    setSelected({
      key,
      tag,
      text: element.textContent || "",
      attrs,
      canEditText: ["text", "tspan"].includes(tag) && element.children.length === 0,
    });
  }

  function handleCanvasClick(event: React.MouseEvent<HTMLDivElement>) {
    const target = event.target instanceof Element ? event.target.closest("[data-editor-key]") : null;
    selectElement(target instanceof SVGElement ? target : null);
  }

  function snapshot(forPersistence = false): string | null {
    const svg = canvasRef.current?.querySelector("svg");
    if (!svg) return null;
    const clone = svg.cloneNode(true) as SVGElement;
    clearEditorSelection(clone);
    if (forPersistence) clone.querySelectorAll("[data-editor-key]").forEach((item) => item.removeAttribute("data-editor-key"));
    return new XMLSerializer().serializeToString(clone);
  }

  function mutateSelected(mutator: (element: SVGElement) => void) {
    if (!selected) return;
    const element = findElement(selected.key);
    const before = snapshot();
    if (!element || !before) return;
    mutator(element);
    const updated = snapshot();
    if (!updated) return;
    setHistory((items) => [...items.slice(-19), before]);
    setSourceMarkup(updated);
    setIsDirty(true);
    selectElement(element);
  }

  function updateAttribute(name: string, value: string) {
    mutateSelected((element) => {
      if (value.trim()) element.setAttribute(name, value.trim());
      else element.removeAttribute(name);
    });
  }

  function updateText(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    mutateSelected((element) => { element.textContent = value; });
  }

  function undo() {
    const previous = history.at(-1);
    if (!previous) return;
    setHistory((items) => items.slice(0, -1));
    setSourceMarkup(previous);
    setSelected(null);
    setIsDirty(true);
  }

  async function save() {
    if (!activeSlideId) return;
    const content = snapshot(true);
    if (!content) return;
    setIsSaving(true);
    setMessage("");
    try {
      const response = await fetch(editorUrl(projectId, jobId, activeSlideId), {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setSourceMarkup(prepareSvg(content));
      setHistory([]);
      setSelected(null);
      setIsDirty(false);
      setExportState("queued");
      setMessage("已保存更改，正在导出新版演示文稿");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败，请稍后重试。");
    } finally {
      setIsSaving(false);
    }
  }

  function chooseSlide(index: number) {
    const next = slides[index];
    if (!next || next.id === activeSlideId) return;
    if (isDirty && !window.confirm("当前页面尚未保存，确定切换吗？")) return;
    setActiveSlideId(next.id);
  }

  return (
    <main className="ppt-editor-shell">
      <header className="ppt-editor-header">
        <button className="editor-back" type="button" onClick={() => window.location.assign("/")}><ArrowLeft size={18} />返回工作台</button>
        <div className="editor-title"><Presentation size={19} /><span>演示文稿编辑器</span><small>{isDirty ? "未保存" : "已保存"}</small></div>
        <div className="editor-header-actions"><button className="editor-icon-button" type="button" title="撤销" aria-label="撤销" disabled={history.length === 0} onClick={undo}><RotateCcw size={17} /></button><button className="editor-save" type="button" disabled={!isDirty || isSaving} onClick={() => void save()}>{isSaving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{isSaving ? "正在保存" : "保存更改"}</button></div>
      </header>
      <div className="ppt-editor-workspace">
        <aside className="editor-slides" aria-label="幻灯片列表"><div className="editor-panel-heading">幻灯片</div><div className="editor-slide-list">{slides.map((slide, index) => <button className={`editor-slide-item ${slide.id === activeSlideId ? "active" : ""}`} type="button" key={slide.id} onClick={() => chooseSlide(index)}><img src={editorUrl(projectId, jobId, slide.id)} alt={`第 ${index + 1} 页缩略图`} /><span>{index + 1}</span></button>)}</div></aside>
        <section className="editor-canvas-panel"><div className="editor-slide-nav"><button className="editor-icon-button" type="button" title="上一页" aria-label="上一页" disabled={activeIndex <= 0} onClick={() => chooseSlide(activeIndex - 1)}><ChevronLeft size={18} /></button><span>{activeIndex >= 0 ? `第 ${activeIndex + 1} / ${slides.length} 页` : "未选择页面"}</span><button className="editor-icon-button" type="button" title="下一页" aria-label="下一页" disabled={activeIndex < 0 || activeIndex >= slides.length - 1} onClick={() => chooseSlide(activeIndex + 1)}><ChevronRight size={18} /></button></div><div className="editor-canvas" ref={canvasRef} onClick={handleCanvasClick}>{isLoading ? <LoaderCircle className="spin" size={24} /> : sourceMarkup ? <div className="editor-svg-stage" dangerouslySetInnerHTML={{ __html: sourceMarkup }} /> : <p>暂无可编辑页面</p>}</div></section>
        <aside className="editor-properties" aria-label="元素属性"><div className="editor-panel-heading">元素属性</div>{selected ? <div className="editor-property-form"><div className="editor-selected-meta"><span>{selected.tag}</span><code>{selected.key}</code></div>{selected.canEditText && <label>文本内容<textarea value={selected.text} onChange={updateText} rows={4} /></label>}{editableAttributes.map((attribute) => <label key={attribute}>{attribute}<input value={selected.attrs[attribute] || ""} onChange={(event) => updateAttribute(attribute, event.target.value)} placeholder="未设置" /></label>)}</div> : <div className="editor-empty-selection"><Type size={22} /><p>在画布中选择元素后编辑</p></div>}<div className="editor-property-actions"><a href={activeSlideId ? editorUrl(projectId, jobId, activeSlideId) : undefined} download className="editor-secondary-action"><Download size={16} />下载当前页 SVG</a><button type="button" className="editor-secondary-action" onClick={() => activeSlideId && window.open(editorUrl(projectId, jobId, activeSlideId), "_blank", "noopener,noreferrer")} disabled={!activeSlideId}><Eye size={16} />查看原始页</button></div>{message && <p className={`editor-message ${exportState === "succeeded" ? "success" : ""}`}>{message}</p>}</aside>
      </div>
    </main>
  );
}
