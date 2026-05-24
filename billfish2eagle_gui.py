"""
Billfish → Eagle 迁移工具 (GUI)
tkinter 零依赖，双击运行。
支持按标签/评分/备注筛选导出，标签层级展平。
"""
import sqlite3
import json
import shutil
import time
import os
import hashlib
import random
import string
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

VERSION = "1.0.0"

# ============================================================
# 核心逻辑（线程安全）
# ============================================================

def sanitize_text(s):
    if not s:
        return ""
    return str(s).encode("utf-8", errors="replace").decode("utf-8")


def id_generator():
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=13))


def file_hash(abs_path):
    """文件 MD5（用于断点续传的唯标识）"""
    h = hashlib.md5()
    try:
        with open(abs_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return abs_path.replace("\\", "/")


def backup_db(db_path, temp_dir):
    """
    WAL 安全数据库备份。
    复制 .db + .db-wal + .db-shm（如存在）到临时目录。
    """
    dest = os.path.join(temp_dir, "billfish_copy.db")
    shutil.copy2(db_path, dest)
    for suffix in ["-wal", "-shm"]:
        side = db_path + suffix
        if os.path.exists(side):
            shutil.copy2(side, dest + suffix)
    return dest


def read_billfish(db_path, lib_root, filter_mode, filter_tags_str,
                  export_tags, export_score, export_note, keep_hierarchy):
    """
    读取 Billfish 数据库。
    filter_mode: "tagged_or_rated" | "tagged" | "rated" | "all" | "specific"
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── Schema 探测 ──
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # 文件夹层级
    c.execute("SELECT id, name, pid FROM bf_folder")
    folder_map = {r["id"]: (r["name"], r["pid"]) for r in c.fetchall()}

    def get_folder_path(folder_id):
        if not folder_id or folder_id == 0:
            return ""
        parts, fid, seen = [], folder_id, set()
        while fid and fid != 0 and fid not in seen:
            if fid not in folder_map:
                break
            seen.add(fid)
            name, pid = folder_map[fid]
            parts.append(name)
            fid = pid
        parts.reverse()
        return "/".join(parts)

    # 标签表：同时读 bf_tag + bf_tag_v2
    tag_pid, tag_name = {}, {}
    for tbl in ["bf_tag_v2", "bf_tag"]:
        if tbl not in tables:
            continue
        try:
            for r in c.execute(f"SELECT id, name, pid FROM {tbl}"):
                tid = r["id"]
                tag_pid[tid] = r["pid"] if "pid" in r.keys() else 0
                tag_name[tid] = r["name"]
        except Exception:
            pass

    def get_tag_full_paths(tag_id):
        paths, parts, cur, seen = [], [], tag_id, set()
        while cur and cur != 0 and cur not in seen:
            if cur not in tag_name:
                break
            seen.add(cur)
            parts.append(sanitize_text(tag_name[cur]))
            cur = tag_pid.get(cur, 0)
        full_parts = list(reversed(parts))
        for i in range(len(full_parts)):
            paths.append("/".join(full_parts[: i + 1]))
        return paths

    # 文件标签关联
    file_tags = {}
    for tbl in ["bf_tag_join_file", "bf_file_tag"]:
        if tbl not in tables:
            continue
        try:
            for r in c.execute(f"SELECT file_id, tag_id FROM {tbl}"):
                fid, tid = r["file_id"], r["tag_id"]
                file_tags.setdefault(fid, set()).add(tid)
        except Exception:
            pass

    # ── 构建查询 ──
    where_parts = []
    if filter_mode == "tagged":
        where_parts.append("EXISTS (SELECT 1 FROM bf_tag_join_file t2 WHERE t2.file_id = f.id)")
    elif filter_mode == "rated":
        where_parts.append("EXISTS (SELECT 1 FROM bf_material_userdata u2 WHERE u2.file_id = f.id AND u2.score > 0)")
    elif filter_mode == "tagged_or_rated":
        where_parts.append(
            "(EXISTS (SELECT 1 FROM bf_material_userdata u2 WHERE u2.file_id = f.id AND u2.score > 0)"
            " OR EXISTS (SELECT 1 FROM bf_tag_join_file t2 WHERE t2.file_id = f.id))"
        )
    # "all": no extra filter

    if filter_mode == "specific" and filter_tags_str:
        wanted = [t.strip() for t in filter_tags_str.replace("，", ",").split(",") if t.strip()]
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where_parts.append(
                f"EXISTS (SELECT 1 FROM bf_tag_join_file tj "
                f"JOIN bf_tag_v2 tv ON tv.id = tj.tag_id "
                f"WHERE tj.file_id = f.id AND tv.name IN ({placeholders}) "
                f"GROUP BY tj.file_id HAVING COUNT(DISTINCT tv.name) >= {len(wanted)})"
            )
            wanted_params = wanted
        else:
            wanted_params = []
    else:
        wanted_params = []

    where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    sql = f"""
        SELECT DISTINCT f.id, f.name, f.pid AS folder_id,
               f.file_size, f.born, f.md5
        FROM bf_file f
        {where_clause}
        ORDER BY f.id
    """
    c.execute(sql, wanted_params)
    file_rows = c.fetchall()

    # 用户数据
    full_udata = {}
    try:
        for r in c.execute("SELECT file_id, score, note, origin FROM bf_material_userdata"):
            full_udata[r["file_id"]] = {
                "score": r["score"] or 0,
                "note": sanitize_text(r["note"] or ""),
                "origin": sanitize_text(r["origin"] or ""),
            }
    except Exception:
        pass

    # ── 构建输出 ──
    items = []
    for row in file_rows:
        fid = row["id"]
        fname = row["name"]
        folder_path = get_folder_path(row["folder_id"])
        filesize = row["file_size"] or 0
        ud = full_udata.get(fid, {})

        abs_path = os.path.join(lib_root, folder_path, fname) if folder_path else os.path.join(lib_root, fname)
        if not os.path.exists(abs_path):
            alt = os.path.join(lib_root, fname)
            abs_path = alt if os.path.exists(alt) else abs_path
        if not os.path.exists(abs_path):
            continue

        tags = []
        if export_tags and keep_hierarchy and fid in file_tags:
            for tid in file_tags[fid]:
                tags.extend(get_tag_full_paths(tid))
        elif export_tags and fid in file_tags:
            for tid in file_tags[fid]:
                if tid in tag_name:
                    tags.append(sanitize_text(tag_name[tid]))
        tags = sorted(set(tags))

        url = ud.get("origin", "")
        if url and str(url).startswith("http"):
            url = str(url)
        else:
            url = ""

        items.append({
            "file_id": fid,
            "filename": fname,
            "abs_path": abs_path,
            "size": filesize,
            "score": ud.get("score", 0) if export_score else 0,
            "tags": tags,
            "note": ud.get("note", "") if export_note else "",
            "url": url,
            "fhash": file_hash(abs_path),
        })

    conn.close()
    return items


# ============================================================
# 迁移工作线程
# ============================================================

class MigrationWorker(threading.Thread):
    def __init__(self, settings, q):
        super().__init__(daemon=True)
        self.settings = settings
        self.q = q       # queue.Queue 用于向 GUI 发送消息
        self._stop = False

    def stop(self):
        self._stop = True

    def log(self, msg):
        self.q.put(("log", msg))

    def progress(self, n, total, ok, err):
        self.q.put(("progress", (n, total, ok, err)))

    def chunked_copy(self, src, dst):
        """分块拷贝，支持中断"""
        size = os.path.getsize(src)
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                if self._stop:
                    return False
                chunk = fsrc.read(1024 * 1024)
                if not chunk:
                    break
                fdst.write(chunk)
        return True

    def run(self):
        s = self.settings
        self.log(f"Billfish → Eagle 迁移 v{VERSION}")

        # ── 验证路径 ──
        if not os.path.exists(s["db_path"]):
            self.q.put(("error", "Billfish 数据库路径不存在"))
            return
        if not os.path.exists(s["lib_root"]):
            self.q.put(("error", "Billfish 资源库根目录不存在"))
            return

        eagle_lib = s["eagle_lib"]
        if not os.path.exists(eagle_lib):
            self.q.put(("error", f"Eagle 库路径不存在: {eagle_lib}"))
            return
        if not eagle_lib.endswith(".library"):
            self.q.put(("error", "Eagle 库路径应以 .library 结尾"))
            return

        # ── WAL 安全备份 ──
        self.log("正在备份数据库（WAL 安全）...")
        temp_dir = os.path.join(os.environ.get("TEMP", "."), "billfish_migrate")
        os.makedirs(temp_dir, exist_ok=True)
        db_copy = backup_db(s["db_path"], temp_dir)

        # ── 磁盘空间预检 ──
        self.log("正在检查磁盘空间...")

        # ── 读取 ──
        self.log("正在读取 Billfish 数据库...")
        try:
            items = read_billfish(
                db_copy, s["lib_root"], s["filter_mode"], s["filter_tags"],
                s["export_tags"], s["export_score"], s["export_note"],
                s["keep_hierarchy"],
            )
        except Exception as e:
            self.q.put(("error", f"数据库读取失败: {e}"))
            return
        self.log(f"共读取 {len(items)} 条素材（筛选模式: {s['filter_mode']}）")

        if not items:
            self.log("没有符合条件的素材需要迁移")
            self.q.put(("done", {"total": 0, "ok": 0, "err": 0}))
            return

        # ── 断点续传 ──
        progress_file = os.path.join(temp_dir, "migration_progress.json")
        done_hashes = set()
        if os.path.exists(progress_file):
            try:
                with open(progress_file, "r", encoding="utf-8") as f:
                    done_hashes = set(json.load(f))
                self.log(f"检测到断点记录: {len(done_hashes)} 条已完成")
            except Exception:
                pass

        # ── 预检磁盘空间 ──
        total_size = sum(it["size"] for it in items if it["fhash"] not in done_hashes)
        try:
            import shutil as _shutil
            free = _shutil.disk_usage(os.path.dirname(eagle_lib + "\\")).free
        except Exception:
            free = 10 * 1024**3  # 兜底
        if total_size > free:
            self.q.put(("error",
                f"磁盘空间不足！需要 {total_size / 1024**2:.0f} MB，"
                f"可用 {free / 1024**2:.0f} MB"))
            return
        self.log(f"待迁移总大小: {total_size / 1024**2:.1f} MB, 可用: {free / 1024**2:.1f} MB")

        # ── 开始写入 ──
        images_dir = os.path.join(eagle_lib, "images")
        os.makedirs(images_dir, exist_ok=True)
        now_ms = int(time.time() * 1000)
        todo = [it for it in items if it["fhash"] not in done_hashes]
        total, ok, err = len(todo), 0, 0

        self.log(f"开始迁移 {total} 条素材 -> {eagle_lib}")
        self.progress(0, total, ok, err)

        new_done = list(done_hashes)

        for i, item in enumerate(todo):
            if self._stop:
                self.log("用户中断，正在保存进度...")
                break

            try:
                eid = id_generator()
                info_dir = os.path.join(images_dir, f"{eid}.info")
                while os.path.exists(info_dir):
                    eid = id_generator()
                    info_dir = os.path.join(images_dir, f"{eid}.info")

                os.makedirs(info_dir, exist_ok=False)

                name_no_ext, ext = os.path.splitext(item["filename"])
                ext = ext.lstrip(".").lower() or "jpg"

                # 拷贝文件（分块 > 100MB）
                dest_file = os.path.join(info_dir, item["filename"])
                if item["size"] > 100 * 1024 * 1024:
                    if not self.chunked_copy(item["abs_path"], dest_file):
                        self.log(f"中断拷贝: {item['filename']}")
                        shutil.rmtree(info_dir, ignore_errors=True)
                        break
                else:
                    shutil.copy2(item["abs_path"], dest_file)

                if self._stop:
                    shutil.rmtree(info_dir, ignore_errors=True)
                    break

                actual_size = os.path.getsize(dest_file)

                meta = {
                    "id": eid,
                    "name": name_no_ext,
                    "size": actual_size,
                    "btime": now_ms,
                    "mtime": now_ms,
                    "ext": ext,
                    "tags": item["tags"],
                    "folders": [],
                    "isDeleted": False,
                    "url": item["url"],
                    "annotation": item["note"],
                    "modificationTime": now_ms,
                    "width": 0,
                    "height": 0,
                    "noThumbnail": False,
                    "lastModified": now_ms,
                    "star": item["score"] if item["score"] > 0 else None,
                    "palettes": [],
                }

                with open(os.path.join(info_dir, "metadata.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)

                ok += 1
                new_done.append(item["fhash"])

                # 每 50 条保存一次进度
                if len(new_done) % 50 == 0:
                    with open(progress_file, "w", encoding="utf-8") as f:
                        json.dump(new_done, f)

            except Exception as e:
                err += 1
                self.log(f"错误: {item['filename']}: {e}")

            self.progress(i + 1, total, ok, err)

        # ── 最终保存进度 ──
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(new_done, f)

        self.log(f"完成: 成功 {ok}, 失败 {err}, 共 {total}")
        self.q.put(("done", {"total": total, "ok": ok, "err": err}))


# ============================================================
# GUI
# ============================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Billfish → Eagle 迁移工具 v{VERSION}")
        self.geometry("720x600")
        self.minsize(640, 480)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.msg_queue = queue.Queue()
        self.worker = None

        # 变量
        self.var_db = tk.StringVar()
        self.var_root = tk.StringVar()
        self.var_eagle = tk.StringVar()
        self.var_export_tags = tk.BooleanVar(value=True)
        self.var_export_score = tk.BooleanVar(value=True)
        self.var_export_note = tk.BooleanVar(value=True)
        self.var_hierarchy = tk.BooleanVar(value=True)
        self.var_filter = tk.StringVar(value="tagged_or_rated")
        self.var_filter_tags = tk.StringVar()

        self._build_ui()
        self._poll_queue()

    # ── UI 构建 ──
    def _build_ui(self):
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        # ── 路径选择 ──
        path_frame = ttk.LabelFrame(main, text="路径设置", padding=5)
        path_frame.pack(fill="x", pady=(0, 5))

        for row, (label, var, is_file) in enumerate([
            ("Billfish 数据库 (.bf/billfish.db):", self.var_db, True),
            ("Billfish 资源库根目录:", self.var_root, False),
            ("Eagle 库 (.library):", self.var_eagle, False),
        ]):
            ttk.Label(path_frame, text=label, width=28, anchor="e").grid(row=row, column=0, sticky="e", padx=(0, 5), pady=2)
            ttk.Entry(path_frame, textvariable=var, width=50).grid(row=row, column=1, sticky="ew", pady=2)
            cmd = lambda v=var, f=is_file: self._browse(v, f)
            ttk.Button(path_frame, text="浏览", command=cmd, width=5).grid(row=row, column=2, padx=(5, 0), pady=2)
        path_frame.columnconfigure(1, weight=1)

        # ── 导出数据 ──
        data_frame = ttk.LabelFrame(main, text="导出数据", padding=5)
        data_frame.pack(fill="x", pady=(0, 5))
        for var, text in [(self.var_export_tags, "标签"), (self.var_export_score, "评分(星级)"), (self.var_export_note, "备注")]:
            ttk.Checkbutton(data_frame, text=text, variable=var).pack(side="left", padx=10)

        # ── 筛选条件 ──
        filter_frame = ttk.LabelFrame(main, text="筛选条件", padding=5)
        filter_frame.pack(fill="x", pady=(0, 5))

        filters = [
            ("有标签或有评分的素材", "tagged_or_rated"),
            ("仅导出有标签的素材", "tagged"),
            ("仅导出有评分的素材", "rated"),
            ("导出全部素材", "all"),
            ("指定标签筛选 (逗号分隔, 交集):", "specific"),
        ]
        for i, (text, value) in enumerate(filters):
            ttk.Radiobutton(filter_frame, text=text, variable=self.var_filter, value=value,
                           command=self._on_filter_change).grid(row=i, column=0, sticky="w", pady=1)

        self.entry_tags = ttk.Entry(filter_frame, textvariable=self.var_filter_tags, width=40, state="disabled")
        self.entry_tags.grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=1)
        filter_frame.columnconfigure(1, weight=1)

        ttk.Checkbutton(filter_frame, text="保留父标签路径 (如 人物/神态)", variable=self.var_hierarchy).grid(
            row=5, column=0, sticky="w", columnspan=2, pady=(5, 0))

        # ── 进度 ──
        progress_frame = ttk.LabelFrame(main, text="进度", padding=5)
        progress_frame.pack(fill="x", pady=(0, 5))
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress_bar.pack(fill="x")
        self.lbl_progress = ttk.Label(progress_frame, text="就绪")
        self.lbl_progress.pack(anchor="w")

        # ── 按钮 ──
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(0, 5))
        self.btn_start = ttk.Button(btn_frame, text="开始迁移", command=self._on_start)
        self.btn_start.pack(side="left", padx=(0, 10))
        self.btn_stop = ttk.Button(btn_frame, text="停止", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left")

        # ── 日志 ──
        log_frame = ttk.LabelFrame(main, text="日志", padding=5)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=10,
                                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _browse(self, var, is_file):
        if is_file:
            path = filedialog.askopenfilename(title="选择 Billfish 数据库", filetypes=[("SQLite", "*.db"), ("All", "*.*")])
        else:
            path = filedialog.askdirectory(title="选择目录")
        if path:
            var.set(path)

    def _on_filter_change(self):
        if self.var_filter.get() == "specific":
            self.entry_tags.configure(state="normal")
        else:
            self.entry_tags.configure(state="disabled")

    def _log(self, msg):
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_start(self):
        db_path = self.var_db.get().strip()
        lib_root = self.var_root.get().strip()
        eagle_lib = self.var_eagle.get().strip()

        if not all([db_path, lib_root, eagle_lib]):
            messagebox.showwarning("路径不完整", "请填写所有三个路径")
            return

        settings = {
            "db_path": db_path,
            "lib_root": lib_root,
            "eagle_lib": eagle_lib,
            "filter_mode": self.var_filter.get(),
            "filter_tags": self.var_filter_tags.get(),
            "export_tags": self.var_export_tags.get(),
            "export_score": self.var_export_score.get(),
            "export_note": self.var_export_note.get(),
            "keep_hierarchy": self.var_hierarchy.get(),
        }

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.progress_bar["value"] = 0
        self.lbl_progress.configure(text="就绪")

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self.worker = MigrationWorker(settings, self.msg_queue)
        self.worker.start()

    def _on_stop(self):
        if self.worker and self.worker.is_alive():
            self._log("正在停止 (请等待当前文件处理完成)...")
            self.worker.stop()
            self.btn_stop.configure(state="disabled")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if messagebox.askyesno("确认", "迁移进行中，确定退出？"):
                self.worker.stop()
                self.destroy()
        else:
            self.destroy()

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind, data = msg
                if kind == "log":
                    self._log(data)
                elif kind == "progress":
                    n, total, ok, err = data
                    if total > 0:
                        self.progress_bar["value"] = n / total * 100
                    self.lbl_progress.configure(text=f"处理: {n}/{total}  成功: {ok}  失败: {err}")
                elif kind == "error":
                    self._log(f"❌ {data}")
                    messagebox.showerror("错误", data)
                    self._reset_buttons()
                elif kind == "done":
                    result = data
                    self._log(f"✅ 迁移完成! 成功 {result['ok']}, 失败 {result['err']}")
                    self.progress_bar["value"] = 100
                    self._reset_buttons()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _reset_buttons(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
