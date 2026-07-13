#!/usr/bin/env python3
"""레가이아 한글 번역 툴 (GUI)

  PROT.DAT 로드 → 전체 대사 표시 → 편집 → PROT.DAT + EXE 빌드

실행:
    python3 tools/translator_gui.py

필요:
    - tools/ 폴더의 다른 도구들 (legaia.py, hook7.py, translate.py, protected.py)
    - tools/Galmuri11.ttf
    - tools/lzss_fast  (없으면 `gcc -O2 -o tools/lzss_fast tools/lzss_fast.c`)
    - 원본 SCUS_942.54 (EXE) — 빌드 시 필요

화면:
    좌: 맵 목록 (번역 진행률)
    중: 대사 목록 (원문 / 번역 / 길이)
    하: 편집기 + 실시간 길이·음절 검사
"""
import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hook7                     # noqa: E402
import translate as T            # noqa: E402

CAP = sum(hook7.capacity().values())          # 400
PROJ = os.path.join(HERE, "..", "build", "project.json")


# ──────────────────────────────────────────────────────────────
# 길이 추정 (편집 중 실시간 표시용)
#   정확한 길이는 페이지 배치에 따라 달라지므로(전환 코드 2B),
#   여기서는 '글자 바이트'만 세고 전환 여유를 따로 보여준다.
# ──────────────────────────────────────────────────────────────
def char_bytes(ko):
    n = 0
    for kind, _ in T.tokenize(ko):
        n += 2 if kind == "h" else 1
    return n


def macro_ok(en, ko):
    return T.macro_counts(en) == T.macro_counts(ko)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("레가이아 한글 번역 툴")
        self.geometry("1280x820")
        self.entries = []
        self.by_map = {}
        self.cur = None
        self.prot_path = None
        self.exe_path = None
        self._build_ui()
        self._load_project()

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self):
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="PROT.DAT 열기", command=self.open_prot).pack(side="left")
        ttk.Button(top, text="EXE 지정", command=self.pick_exe).pack(side="left", padx=4)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(top, text="저장", command=self.save).pack(side="left")
        ttk.Button(top, text="빌드 → PROT.DAT", command=self.build).pack(side="left", padx=4)
        self.lbl_stat = ttk.Label(top, text="PROT.DAT 를 열어 주세요")
        self.lbl_stat.pack(side="right")

        # 음절 사용량
        bar = ttk.Frame(self, padding=(6, 0))
        bar.pack(fill="x")
        self.lbl_syl = ttk.Label(bar, text=f"음절 0 / {CAP}")
        self.lbl_syl.pack(side="left")
        self.pb = ttk.Progressbar(bar, length=260, maximum=CAP)
        self.pb.pack(side="left", padx=8)
        self.lbl_warn = ttk.Label(bar, text="", foreground="#c00")
        self.lbl_warn.pack(side="left", padx=10)

        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=6, pady=6)

        # 맵 목록
        left = ttk.Frame(pan)
        self.maps = ttk.Treeview(left, columns=("n", "p"), show="headings", height=30)
        self.maps.heading("n", text="맵")
        self.maps.heading("p", text="진행")
        self.maps.column("n", width=70, anchor="e")
        self.maps.column("p", width=90, anchor="center")
        self.maps.pack(fill="both", expand=True)
        self.maps.bind("<<TreeviewSelect>>", self.on_map)
        pan.add(left, weight=0)

        # 대사 목록
        mid = ttk.Frame(pan)
        cols = ("id", "en", "ko", "len")
        self.rows = ttk.Treeview(mid, columns=cols, show="headings")
        for c, t, w in (("id", "위치", 110), ("en", "원문", 380),
                        ("ko", "번역", 380), ("len", "길이", 90)):
            self.rows.heading(c, text=t)
            self.rows.column(c, width=w, anchor="w")
        self.rows.column("len", anchor="center")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.rows.yview)
        self.rows.configure(yscrollcommand=vs.set)
        self.rows.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.rows.bind("<<TreeviewSelect>>", self.on_row)
        self.rows.tag_configure("over", background="#ffe0e0")
        self.rows.tag_configure("done", background="#e8f6e8")
        self.rows.tag_configure("badmac", background="#ffe8c0")
        pan.add(mid, weight=1)

        # 편집기
        ed = ttk.LabelFrame(self, text="편집", padding=6)
        ed.pack(fill="x", padx=6, pady=(0, 6))
        self.lbl_en = ttk.Label(ed, text="", foreground="#444", font=("", 10))
        self.lbl_en.pack(anchor="w")
        row = ttk.Frame(ed)
        row.pack(fill="x", pady=4)
        self.ent = tk.Text(row, height=2, font=("", 12), wrap="word")
        self.ent.pack(side="left", fill="x", expand=True)
        self.ent.bind("<KeyRelease>", self.on_edit)
        self.ent.bind("<Control-Return>", lambda e: self.commit())
        side = ttk.Frame(row)
        side.pack(side="left", padx=6)
        self.lbl_len = ttk.Label(side, text="—", font=("", 14, "bold"))
        self.lbl_len.pack()
        ttk.Button(side, text="적용 (Ctrl+Enter)", command=self.commit).pack(pady=2)
        self.lbl_hint = ttk.Label(ed, text="", foreground="#c00")
        self.lbl_hint.pack(anchor="w")

    # ── 데이터 ─────────────────────────────────────────────
    def open_prot(self):
        p = filedialog.askopenfilename(title="PROT.DAT",
                                       filetypes=[("PROT.DAT", "*.DAT *.dat"), ("모든 파일", "*")])
        if not p:
            return
        self.prot_path = p
        self.lbl_stat.config(text="대사 추출 중…")
        self.update_idletasks()
        threading.Thread(target=self._dump, daemon=True).start()

    def _dump(self):
        try:
            import dump_text
            dump_text.PROT = self.prot_path
            out = os.path.join(HERE, "..", "build", "text_dump.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            dump_text.OUT = out
            dump_text.main()
            new = json.load(open(out, encoding="utf-8"))
        except Exception as ex:
            self.after(0, lambda: messagebox.showerror("실패", str(ex)))
            return
        # 기존 번역 승계 (원문 기준)
        old = {e["text"]: e["ko"] for e in self.entries if e.get("ko")}
        for e in new:
            if not e.get("ko") and e["text"] in old:
                e["ko"] = old[e["text"]]
        self.entries = new
        self.after(0, self._refresh_maps)

    def _load_project(self):
        if os.path.exists(PROJ):
            try:
                d = json.load(open(PROJ, encoding="utf-8"))
                self.prot_path = d.get("prot")
                self.exe_path = d.get("exe")
                dump = os.path.join(HERE, "..", "build", "text_dump.json")
                if os.path.exists(dump):
                    self.entries = json.load(open(dump, encoding="utf-8"))
                    self._refresh_maps()
            except Exception:
                pass

    def _refresh_maps(self):
        self.by_map = {}
        for e in self.entries:
            self.by_map.setdefault(e["map"], []).append(e)
        self.maps.delete(*self.maps.get_children())
        for m in sorted(self.by_map):
            es = self.by_map[m]
            done = sum(1 for e in es if e.get("ko"))
            self.maps.insert("", "end", iid=str(m),
                             values=(m, f"{done}/{len(es)}"))
        self.lbl_stat.config(
            text=f"{len(self.entries):,}런 / {len(self.by_map)}맵  |  {os.path.basename(self.prot_path or '')}")
        self._update_syl()

    def on_map(self, _=None):
        sel = self.maps.selection()
        if not sel:
            return
        m = int(sel[0])
        self.rows.delete(*self.rows.get_children())
        for i, e in enumerate(self.by_map[m]):
            self.rows.insert("", "end", iid=str(i), values=self._row_vals(e),
                             tags=self._row_tag(e))

    def _row_vals(self, e):
        ko = e.get("ko", "")
        n = char_bytes(ko) if ko else 0
        return (f"s{e['script']} r{e['run']}", e["text"], ko,
                f"{n}/{e['len']}" if ko else f"—/{e['len']}")

    def _row_tag(self, e):
        ko = e.get("ko", "")
        if not ko:
            return ()
        if not macro_ok(e["text"], ko):
            return ("badmac",)
        if char_bytes(ko) > e["len"]:
            return ("over",)
        return ("done",)

    # ── 편집 ──────────────────────────────────────────────
    def on_row(self, _=None):
        sel = self.rows.selection()
        msel = self.maps.selection()
        if not sel or not msel:
            return
        e = self.by_map[int(msel[0])][int(sel[0])]
        self.cur = e
        self.lbl_en.config(text=f"원문 ({e['len']}B):  {e['text']}")
        self.ent.delete("1.0", "end")
        self.ent.insert("1.0", e.get("ko", ""))
        self.on_edit()

    def on_edit(self, _=None):
        if not self.cur:
            return
        ko = self.ent.get("1.0", "end-1c")
        lim = self.cur["len"]
        n = char_bytes(ko)
        # 페이지 전환 여유(각 2B)를 감안해 경고
        room = lim - n
        if n == 0:
            self.lbl_len.config(text=f"—/{lim}", foreground="#666")
        elif n > lim:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#c00")
        elif room < 2:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#e07000")
        else:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#080")

        msgs = []
        if n > lim:
            msgs.append(f"★ {n - lim}B 초과 — 줄여야 삽입됨")
        elif ko and room < 2:
            msgs.append("⚠ 페이지 전환(2B)이 붙으면 초과할 수 있음")
        if ko and not macro_ok(self.cur["text"], ko):
            a = T.macro_counts(self.cur["text"])
            b = T.macro_counts(ko)
            miss = {k: v for k, v in a.items() if b.get(k, 0) < v}
            ext = {k: v for k, v in b.items() if a.get(k, 0) < v}
            if miss:
                msgs.append(f"🔴 매크로 누락 {dict(miss)} — 게임이 멈춥니다!")
            if ext:
                msgs.append(f"🔴 매크로 추가됨 {dict(ext)}")
        self.lbl_hint.config(text="   ".join(msgs))

    def commit(self):
        if not self.cur:
            return
        self.cur["ko"] = self.ent.get("1.0", "end-1c").strip()
        sel = self.rows.selection()
        if sel:
            self.rows.item(sel[0], values=self._row_vals(self.cur),
                           tags=self._row_tag(self.cur))
        msel = self.maps.selection()
        if msel:
            m = int(msel[0])
            es = self.by_map[m]
            done = sum(1 for e in es if e.get("ko"))
            self.maps.item(msel[0], values=(m, f"{done}/{len(es)}"))
        self._update_syl()
        # 다음 미번역으로 이동
        if sel:
            nxt = self.rows.next(sel[0])
            if nxt:
                self.rows.selection_set(nxt)
                self.rows.see(nxt)

    def _update_syl(self):
        trans = [e for e in self.entries if e.get("ko")]
        freq = T.collect_syllables(trans)
        n = len(freq)
        self.pb["value"] = min(n, CAP)
        self.lbl_syl.config(text=f"음절 {n} / {CAP}")
        w = []
        if n > CAP:
            w.append(f"★ 음절 {n - CAP}개 초과 — 일부 대사가 안 나옵니다")
        bad = sum(1 for e in trans if not macro_ok(e["text"], e["ko"]))
        if bad:
            w.append(f"🔴 매크로 오류 {bad}건")
        over = sum(1 for e in trans if char_bytes(e["ko"]) > e["len"])
        if over:
            w.append(f"길이 초과 {over}건")
        self.lbl_warn.config(text="   ".join(w))

    # ── 저장/빌드 ─────────────────────────────────────────
    def save(self):
        out = os.path.join(HERE, "..", "build", "text_dump.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(self.entries, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump({"prot": self.prot_path, "exe": self.exe_path},
                  open(PROJ, "w", encoding="utf-8"), ensure_ascii=False)
        n = sum(1 for e in self.entries if e.get("ko"))
        messagebox.showinfo("저장", f"번역 {n}런 저장됨\n{out}")

    def pick_exe(self):
        p = filedialog.askopenfilename(title="원본 EXE (SCUS_942.54)")
        if p:
            self.exe_path = p
            messagebox.showinfo("EXE", os.path.basename(p))

    def build(self):
        if not self.prot_path:
            messagebox.showwarning("빌드", "PROT.DAT 를 먼저 열어 주세요")
            return
        if not self.exe_path:
            messagebox.showwarning("빌드", "원본 EXE 를 지정해 주세요 (SCUS_942.54)")
            return
        outdir = filedialog.askdirectory(title="출력 폴더")
        if not outdir:
            return
        self.save()
        self.lbl_stat.config(text="빌드 중…")
        self.update_idletasks()
        threading.Thread(target=self._build, args=(outdir,), daemon=True).start()

    def _build(self, outdir):
        try:
            T.PROT_US = self.prot_path
            T.EXE_US = self.exe_path
            T.OUT_PROT = os.path.join(outdir, "PROT.DAT")
            T.OUT_EXE = os.path.join(outdir, "SCUS_942.54")
            T.DUMP = os.path.join(HERE, "..", "build", "text_dump.json")
            T.build_patch(prot_in=self.prot_path, exe_in=self.exe_path,
                          out_prot=T.OUT_PROT, out_exe=T.OUT_EXE)
        except SystemExit as ex:
            self.after(0, lambda: messagebox.showerror("빌드 실패 (가드)", str(ex)))
            self.after(0, lambda: self.lbl_stat.config(text="빌드 실패"))
            return
        except Exception as ex:
            self.after(0, lambda: messagebox.showerror("빌드 실패", str(ex)))
            self.after(0, lambda: self.lbl_stat.config(text="빌드 실패"))
            return
        self.after(0, lambda: messagebox.showinfo(
            "빌드 완료",
            f"{outdir}\n\n  PROT.DAT\n  SCUS_942.54\n\n"
            "이 두 파일을 디스크 이미지에 써 넣으면 됩니다.\n"
            "(tools/write_disc.py 사용)"))
        self.after(0, lambda: self.lbl_stat.config(text="빌드 완료"))


if __name__ == "__main__":
    App().mainloop()
