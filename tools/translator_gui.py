#!/usr/bin/env python3
"""레가이아 한글 번역 툴 (GUI) — 맵별 저장 + 검색

핵심 개선:
  - 맵별 분할 저장 (build/maps/map6.json ...): 큰 파일 저장 실패 문제 해결
  - 편집 즉시 그 맵만 저장 (작고 안전)
  - 영어/한글 검색으로 특정 대사를 빨리 찾아 수정
  - 실시간 길이/음절/매크로 검사

실행:
    python3 tools/translator_gui.py

처음이면:
    python3 tools/dump_text.py            # PROT -> text_dump.json
    python3 tools/project_store.py        # -> build/maps/*.json 로 분할
"""
import os
import re
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import translate as T          # noqa: E402
import hook7                   # noqa: E402
import project_store as PS     # noqa: E402

CAP = sum(hook7.capacity().values())


def char_bytes(ko):
    return sum(2 if k == "h" else 1 for k, _ in T.tokenize(ko))


def macro_pattern(text):
    """매크로 파라미터를 지운 '패턴'.  'the {ITEM:77}!' -> 'the {ITEM}!'

    아이템마다 파라미터가 달라 같은 문장이 수십 개로 흩어져 있다.
    패턴이 같으면 같은 번역을 쓸 수 있다 (파라미터만 갈아끼우면 된다).
    """
    return re.sub(r"\{(\w+):[0-9A-Fa-f]{2}\}", r"{\1}", text)


def retarget_macros(ko_template, target_text):
    """번역문의 매크로를 target_text 의 실제 매크로(파라미터 포함)로 바꾼다.

    예:  ko_template='{ITEM}이(가)',  target_text='There is a {ITEM:7A}'
         ->  '{ITEM:7A}이(가)'
    """
    src = re.findall(r"\{\w+(?::[0-9A-Fa-f]{2})?\}", target_text)
    out = ko_template
    for m in src:
        name = m[1:].split(":")[0].rstrip("}")
        # 번역문의 같은 이름 매크로(파라미터 없거나 다른 것)를 target 것으로
        out = re.sub(r"\{" + name + r"(?::[0-9A-Fa-f]{2})?\}", m.replace("\\", ""),
                     out, count=1)
    return out


def macro_ok(en, ko):
    return T.macro_counts(en) == T.macro_counts(ko)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("레가이아 한글 번역 툴")
        self.geometry("1300x860")
        self.maps = {}            # map_id -> [runs]  (로드된 것만)
        self.cur_map = None
        self.cur_run = None
        self.rows_data = []       # 현재 표시 중인 런 리스트
        self.cut_syl = set()      # 폰트 칸 초과로 잘릴 음절
        self._build_ui()
        self._refresh_maplist()

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self):
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        ttk.Button(top, text="PROT.DAT 열기", command=self.import_prot).pack(side="left")
        ttk.Button(top, text="전체 저장", command=self.save_all).pack(side="left", padx=4)
        ttk.Button(top, text="빌드", command=self.build).pack(side="left")
        self.lbl_stat = ttk.Label(top, text="")
        self.lbl_stat.pack(side="right")

        # 검색 바
        sb = ttk.Frame(self, padding=(6, 0))
        sb.pack(fill="x")
        ttk.Label(sb, text="검색:").pack(side="left")
        self.q = tk.StringVar()
        ent = ttk.Entry(sb, textvariable=self.q, width=40)
        ent.pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self.search())
        ttk.Button(sb, text="찾기 (영어/한글)", command=self.search).pack(side="left")
        ttk.Button(sb, text="★ 빈출순", command=self.show_frequent).pack(side="left", padx=(6, 0))
        ttk.Button(sb, text="패턴순", command=self.show_patterns).pack(side="left", padx=(2, 0))
        self.scope = tk.StringVar(value="all")
        ttk.Radiobutton(sb, text="전체 맵", variable=self.scope,
                        value="all").pack(side="left", padx=(10, 0))
        ttk.Radiobutton(sb, text="현재 맵", variable=self.scope,
                        value="cur").pack(side="left")
        self.only_untr = tk.BooleanVar(value=False)
        ttk.Checkbutton(sb, text="미번역만", variable=self.only_untr).pack(side="left", padx=8)
        # ★ 같은 원문 자동 전파 (중복 대사가 많아 작업량이 크게 준다)
        self.propagate = tk.BooleanVar(value=True)
        ttk.Checkbutton(sb, text="같은 원문에 자동 적용",
                        variable=self.propagate).pack(side="left")
        self.lbl_syl = ttk.Label(sb, text=f"음절 0/{CAP}")
        self.lbl_syl.pack(side="right")

        pan = ttk.PanedWindow(self, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=6, pady=6)

        # 맵 목록
        left = ttk.Frame(pan)
        ttk.Label(left, text="맵").pack(anchor="w")
        self.maplist = ttk.Treeview(left, columns=("m", "p"), show="headings", height=30)
        self.maplist.heading("m", text="맵")
        self.maplist.heading("p", text="진행")
        self.maplist.column("m", width=64, anchor="e")
        self.maplist.column("p", width=88, anchor="center")
        self.maplist.pack(fill="both", expand=True)
        self.maplist.bind("<<TreeviewSelect>>", self.on_map)
        pan.add(left, weight=0)

        # 대사 목록
        mid = ttk.Frame(pan)
        self.lbl_list = ttk.Label(mid, text="대사")
        self.lbl_list.pack(anchor="w")
        cols = ("loc", "en", "ko", "len")
        self.rows = ttk.Treeview(mid, columns=cols, show="headings")
        for c, t, w in (("loc", "위치", 120), ("en", "원문", 360),
                        ("ko", "번역", 360), ("len", "길이", 80)):
            self.rows.heading(c, text=t)
            self.rows.column(c, width=w, anchor="w")
        self.rows.column("len", anchor="center")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.rows.yview)
        self.rows.configure(yscrollcommand=vs.set)
        self.rows.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.rows.bind("<<TreeviewSelect>>", self.on_row)
        self.rows.bind("<Button-3>", self._ctx_row)
        self.rows.tag_configure("over", background="#ffe0e0")
        self.rows.tag_configure("done", background="#e8f6e8")
        self.rows.tag_configure("badmac", background="#ffe8c0")
        pan.add(mid, weight=1)

        # 편집기
        ed = ttk.LabelFrame(self, text="편집  (Ctrl+Enter 적용→저장,  ↓ 다음)", padding=6)
        ed.pack(fill="x", padx=6, pady=(0, 6))
        # 원문: 드래그 선택 + 복사 가능하도록 Text 로 (읽기 전용)
        enrow = ttk.Frame(ed)
        enrow.pack(fill="x")
        self.lbl_loc = ttk.Label(enrow, text="", foreground="#666", width=22)
        self.lbl_loc.pack(side="left")
        self.txt_en = tk.Text(enrow, height=1, font=("", 11), wrap="none",
                              bg="#f4f4f4", relief="flat", cursor="xterm")
        self.txt_en.pack(side="left", fill="x", expand=True)
        self.txt_en.bind("<Key>", lambda e: "break")      # 편집 금지, 선택은 가능
        self.txt_en.bind("<Button-3>", self._ctx_en)
        ttk.Button(enrow, text="원문 복사", width=10,
                   command=self.copy_en).pack(side="left", padx=4)
        row = ttk.Frame(ed)
        row.pack(fill="x", pady=4)
        self.ent = tk.Text(row, height=2, font=("", 13), wrap="word")
        self.ent.pack(side="left", fill="x", expand=True)
        self.ent.bind("<KeyRelease>", self.on_edit)
        self.ent.bind("<Control-Return>", lambda e: (self.commit(), "break"))
        side = ttk.Frame(row)
        side.pack(side="left", padx=6)
        self.lbl_len = ttk.Label(side, text="—", font=("", 15, "bold"))
        self.lbl_len.pack()
        ttk.Button(side, text="적용", command=self.commit).pack(pady=2)
        self.lbl_hint = ttk.Label(ed, text="", foreground="#c00")
        self.lbl_hint.pack(anchor="w")

    # ── 맵 목록/로드 ───────────────────────────────────────
    def _refresh_maplist(self):
        self.maplist.delete(*self.maplist.get_children())
        st = PS.stats()
        if not st:
            self.lbl_stat.config(text="build/maps 가 비어 있음 — PROT.DAT 를 열거나 project_store 로 분할")
            return
        tot = sum(n for _, n in st.values())
        done = sum(d for d, _ in st.values())
        for m in sorted(st):
            d, n = st[m]
            self.maplist.insert("", "end", iid=str(m), values=(m, f"{d}/{n}"))
        self.lbl_stat.config(text=f"{len(st)}맵 · {tot:,}런 · 번역 {done}")
        self._update_syl()

    def _get_map(self, m):
        if m not in self.maps:
            self.maps[m] = PS.load_map(m)
        return self.maps[m]

    def on_map(self, _=None):
        sel = self.maplist.selection()
        if not sel:
            return
        m = int(sel[0])
        self.cur_map = m
        runs = self._get_map(m)
        self._show_rows(runs, f"맵 {m} — {len(runs)}런")

    def _show_rows(self, runs, title):
        self.rows_data = runs
        self.lbl_list.config(text=title)
        self.rows.delete(*self.rows.get_children())
        for i, e in enumerate(runs):
            self.rows.insert("", "end", iid=str(i),
                             values=self._vals(e), tags=self._tag(e))

    def _vals(self, e):
        ko = e.get("ko", "")
        n = char_bytes(ko) if ko else 0
        # 빈출 뷰에서는 반복 횟수를 보여준다 (몇 곳에 적용되는지)
        f = getattr(self, "freq", None)
        if f and f.get(e["text"], 1) > 1:
            loc = f"×{f[e['text']]}  m{e['map']}"
        else:
            loc = f"m{e['map']} s{e['script']}r{e['run']}"
        return (loc, e["text"], ko, f"{n}/{e['len']}" if ko else f"—/{e['len']}")

    def _tag(self, e):
        ko = e.get("ko", "")
        if not ko:
            return ()
        if not macro_ok(e["text"], ko):
            return ("badmac",)
        if char_bytes(ko) > e["len"]:
            return ("over",)
        return ("done",)

    # ── 복사 ──────────────────────────────────────────────
    def copy_en(self):
        """원문 전체를 클립보드로 (선택 영역이 있으면 그 부분만)."""
        try:
            t = self.txt_en.get("sel.first", "sel.last")
        except tk.TclError:
            t = self.txt_en.get("1.0", "end-1c")
        if t:
            self.clipboard_clear()
            self.clipboard_append(t)

    def _ctx_en(self, ev):
        """원문 위 우클릭 메뉴."""
        m = tk.Menu(self, tearoff=0)
        try:
            self.txt_en.get("sel.first", "sel.last")
            m.add_command(label="선택 영역 복사", command=self.copy_en)
        except tk.TclError:
            pass
        m.add_command(label="원문 전체 복사", command=self.copy_en)
        m.add_separator()
        m.add_command(label="원문을 번역칸에 붙여넣기",
                      command=lambda: (self.ent.delete("1.0", "end"),
                                       self.ent.insert("1.0", self.txt_en.get("1.0", "end-1c"))))
        m.tk_popup(ev.x_root, ev.y_root)

    def _ctx_row(self, ev):
        """대사 목록 우클릭 메뉴."""
        iid = self.rows.identify_row(ev.y)
        if not iid:
            return
        self.rows.selection_set(iid)
        e = self.rows_data[int(iid)]
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="원문 복사",
                      command=lambda: (self.clipboard_clear(),
                                       self.clipboard_append(e["text"])))
        if e.get("ko"):
            m.add_command(label="번역 복사",
                          command=lambda: (self.clipboard_clear(),
                                           self.clipboard_append(e["ko"])))
            m.add_separator()
            m.add_command(label="번역 지우기",
                          command=lambda: self._clear_ko(e, iid))
        m.tk_popup(ev.x_root, ev.y_root)

    def _clear_ko(self, e, iid):
        e["ko"] = ""
        self.rows.item(iid, values=self._vals(e), tags=self._tag(e))
        PS.save_map(e["map"], self._get_map(e["map"]))
        self._refresh_one_map(e["map"])

    def _refresh_one_map(self, m):
        runs = self._get_map(m)
        done = sum(1 for x in runs if x.get("ko"))
        if self.maplist.exists(str(m)):
            self.maplist.item(str(m), values=(m, f"{done}/{len(runs)}"))

    # ── 검색 ──────────────────────────────────────────────
    def show_frequent(self):
        """★ 많이 반복되는 대사부터 — 하나 번역하면 여러 곳이 채워진다.

        예: '{NAME:63} now has' 는 253회, 'The treasure chest is empty!' 는 191회.
        빈출순으로 훑으면 적은 노동으로 넓게 커버된다.
        """
        from collections import Counter
        pool = []
        for m in PS.list_maps():
            pool.extend(self._get_map(m))
        cnt = Counter(e["text"] for e in pool)
        seen = set()
        hits = []
        for e in sorted(pool, key=lambda x: -cnt[x["text"]]):
            if e["text"] in seen:
                continue
            if self.only_untr.get() and e.get("ko"):
                continue
            seen.add(e["text"])
            hits.append(e)
        self.freq = cnt
        self._show_rows(hits, f"빈출순 — 고유 {len(hits)}개 (각 1행, 번역하면 같은 원문 전체 적용)")

    def show_patterns(self):
        """★ 패턴순 — 매크로 파라미터만 다른 대사를 한 덩어리로 본다.

        'the {ITEM:77}!' 'the {ITEM:78}!' 'the {ITEM:7A}!' ... 는 사실 같은 문장이다
        (아이템마다 파라미터가 다를 뿐). 이런 게 259개나 흩어져 있다.
        하나 번역하면 나머지도 파라미터만 갈아끼워 자동으로 채워진다.
        """
        from collections import Counter
        pool = []
        for m in PS.list_maps():
            pool.extend(self._get_map(m))
        cnt = Counter(macro_pattern(e["text"]) for e in pool)
        seen = set()
        hits = []
        for e in sorted(pool, key=lambda x: -cnt[macro_pattern(x["text"])]):
            p = macro_pattern(e["text"])
            if p in seen:
                continue
            if self.only_untr.get() and e.get("ko"):
                continue
            seen.add(p)
            hits.append(e)
        self.freq = {e["text"]: cnt[macro_pattern(e["text"])] for e in pool}
        self._show_rows(hits, f"패턴순 — {len(hits)}개 (파라미터만 다른 같은 문장을 묶음)")

    def search(self):
        q = self.q.get().strip()
        if not q:
            return
        ql = q.lower()
        pool = []
        if self.scope.get() == "cur" and self.cur_map is not None:
            pool = self._get_map(self.cur_map)
        else:
            for m in PS.list_maps():
                pool.extend(self._get_map(m))
        hits = []
        for e in pool:
            if self.only_untr.get() and e.get("ko"):
                continue
            if ql in e["text"].lower() or ql in e.get("ko", "").lower():
                hits.append(e)
        self._show_rows(hits, f"검색 '{q}' — {len(hits)}건")

    # ── 편집 ──────────────────────────────────────────────
    def on_row(self, _=None):
        sel = self.rows.selection()
        if not sel:
            return
        e = self.rows_data[int(sel[0])]
        self.cur_run = e
        self.lbl_loc.config(text=f"[m{e['map']} s{e['script']}r{e['run']}]  {e['len']}B")
        self.txt_en.delete("1.0", "end")
        self.txt_en.insert("1.0", e["text"])
        self.ent.delete("1.0", "end")
        self.ent.insert("1.0", e.get("ko", ""))
        self.ent.focus_set()
        self.on_edit()

    def on_edit(self, _=None):
        if not self.cur_run:
            return
        ko = self.ent.get("1.0", "end-1c")
        lim = self.cur_run["len"]
        n = char_bytes(ko)
        if n == 0:
            self.lbl_len.config(text=f"—/{lim}", foreground="#666")
        elif n > lim:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#c00")
        elif lim - n < 2:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#e07000")
        else:
            self.lbl_len.config(text=f"{n}/{lim}", foreground="#080")
        msgs = []
        if n > lim:
            msgs.append(f"★ {n-lim}B 초과 — 줄여야 삽입됨")
        elif ko and lim - n < 2:
            msgs.append("⚠ 페이지 전환(2B) 붙으면 초과 가능")
        if ko and not macro_ok(self.cur_run["text"], ko):
            a = T.macro_counts(self.cur_run["text"])
            b = T.macro_counts(ko)
            miss = {k: v for k, v in a.items() if b.get(k, 0) < v}
            if miss:
                msgs.append(f"🔴 매크로 누락 {dict(miss)} — 게임 멈춤!")
            ext = {k: v for k, v in b.items() if a.get(k, 0) < v}
            if ext:
                msgs.append(f"🔴 매크로 추가됨 {dict(ext)}")
        # ★ 폰트 칸이 모자라 잘릴 음절을 쓰고 있으면 경고
        #   (그 음절이 있으면 이 대사는 게임에서 영어로 나온다)
        cut = getattr(self, "cut_syl", set())
        if ko and cut:
            used = sorted({c for c in ko if c in cut})
            if used:
                msgs.append(f"🔴 폰트에 없는 음절 '{''.join(used)}' — 이 대사는 영어로 나옵니다")

        self.lbl_hint.config(text="   ".join(msgs))

    def commit(self):
        if not self.cur_run:
            return
        ko = self.ent.get("1.0", "end-1c").strip()
        self.cur_run["ko"] = ko
        src = self.cur_run["text"]

        # ★ 자동 전파 — 두 단계
        #   1) 같은 원문        : '{NAME:63} now has' 는 253회 나온다
        #   2) 같은 '패턴'      : 'the {ITEM:77}!' 'the {ITEM:78}!' ... 는 사실 같은 문장.
        #      매크로 파라미터만 갈아끼워 채운다 (259개가 흩어져 있다).
        touched = {self.cur_run["map"]}
        n_prop = 0
        if self.propagate.get() and ko:
            pat = macro_pattern(src)
            for m in PS.list_maps():
                runs = self._get_map(m)
                hit = False
                for e in runs:
                    if e is self.cur_run or e.get("ko"):
                        continue
                    if e["text"] == src:
                        new_ko = ko
                    elif macro_pattern(e["text"]) == pat:
                        new_ko = retarget_macros(ko, e["text"])   # 파라미터 교체
                    else:
                        continue
                    # 매크로/길이가 맞을 때만
                    if T.macro_counts(e["text"]) != T.macro_counts(new_ko):
                        continue
                    if char_bytes(new_ko) > e["len"]:
                        continue
                    e["ko"] = new_ko
                    n_prop += 1
                    hit = True
                if hit:
                    touched.add(m)

        # 화면 갱신
        sel = self.rows.selection()
        if sel:
            self.rows.item(sel[0], values=self._vals(self.cur_run),
                           tags=self._tag(self.cur_run))
        # 현재 목록에 같은 원문이 또 있으면 같이 갱신
        if n_prop:
            for i, e in enumerate(self.rows_data):
                if e["text"] == src and self.rows.exists(str(i)):
                    self.rows.item(str(i), values=self._vals(e), tags=self._tag(e))

        # ★ 바뀐 맵들만 저장 (작고 안전)
        for m in touched:
            PS.save_map(m, self._get_map(m))
            self._refresh_one_map(m)
        self._update_syl()

        if n_prop:
            self.lbl_hint.config(text=f"↳ 같은 원문 {n_prop}곳에 함께 적용됨",
                                 foreground="#080")

        # 다음 행으로
        if sel:
            nxt = self.rows.next(sel[0])
            if nxt:
                self.rows.selection_set(nxt)
                self.rows.see(nxt)

    def _update_syl(self):
        """음절 사용량 표시. 폰트 칸을 넘으면 '잘릴 음절'을 알려준다.

        폰트는 400칸뿐이라, 넘치는 음절은 게임에 못 들어가고
        그 음절을 쓰는 대사는 **영어로 남는다**. 미리 알려줘야
        번역을 다듬어 피할 수 있다.
        """
        allruns = []
        for m in self.maps:                    # 로드된 맵만 (빠르게)
            allruns.extend(self.maps[m])
        trans = [e for e in allruns if e.get("ko")]
        if not trans:
            self.lbl_syl.config(text=f"음절 0/{CAP}", foreground="#080")
            self.cut_syl = set()
            return
        freq = T.collect_syllables(trans)
        n = len(freq)
        if n > CAP:
            # 빈도가 낮은 것부터 잘린다
            order = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
            self.cut_syl = {c for c, _ in order[CAP:]}
            self.lbl_syl.config(
                text=f"음절 {n}/{CAP}  🔴 {n-CAP}자 초과 (잘림)",
                foreground="#c00")
        else:
            self.cut_syl = set()
            self.lbl_syl.config(text=f"음절 {n}/{CAP}", foreground="#080")

    # ── 저장/빌드 ─────────────────────────────────────────
    def save_all(self):
        for m, runs in self.maps.items():
            PS.save_map(m, runs)
        messagebox.showinfo("저장", f"{len(self.maps)}개 맵 저장 완료\n{PS.MAPS_DIR}")

    def import_prot(self):
        p = filedialog.askopenfilename(title="PROT.DAT",
                                       filetypes=[("PROT.DAT", "*.DAT *.dat"), ("모든 파일", "*")])
        if not p:
            return
        try:
            import dump_text
            dump_text.PROT = p
            out = os.path.join(HERE, "..", "build", "text_dump.json")
            dump_text.OUT = out
            dump_text.main()
            # 기존 번역 승계 후 분할
            old = {}
            for m in PS.list_maps():
                for e in PS.load_map(m):
                    if e.get("ko"):
                        old[(e["map"], e["script"], e["run"])] = e["ko"]
            d = json.load(open(out, encoding="utf-8"))
            for e in d:
                k = (e["map"], e["script"], e["run"])
                if k in old and not e.get("ko"):
                    e["ko"] = old[k]
            json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            PS.split_dump(out)
            self.maps.clear()
            self._refresh_maplist()
            messagebox.showinfo("완료", f"{len(d):,}런 로드 · 맵별 분할 저장")
        except Exception as ex:
            messagebox.showerror("실패", str(ex))

    def build(self):
        # 맵별 파일 -> text_dump.json 합치고 빌드
        self.save_all()
        dump = os.path.join(HERE, "..", "build", "text_dump.json")
        n = PS.merge_to_dump(dump)
        prot = filedialog.askopenfilename(title="원본 PROT.DAT")
        if not prot:
            return
        exe = filedialog.askopenfilename(title="원본 EXE (SCUS_942.54)")
        if not exe:
            return
        outdir = filedialog.askdirectory(title="출력 폴더")
        if not outdir:
            return
        try:
            T.build_patch(prot_in=prot, exe_in=exe,
                          out_prot=os.path.join(outdir, "PROT.DAT"),
                          out_exe=os.path.join(outdir, "SCUS_942.54"),
                          dump=dump)
            messagebox.showinfo("빌드 완료",
                                f"{outdir}\n  PROT.DAT\n  SCUS_942.54\n\n"
                                "write_disc.py 로 디스크에 써 넣으세요.")
        except SystemExit as ex:
            messagebox.showerror("빌드 실패 (가드)", str(ex))
        except Exception as ex:
            messagebox.showerror("빌드 실패", str(ex))


if __name__ == "__main__":
    App().mainloop()
