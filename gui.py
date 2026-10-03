#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面：全程只开一个 Tk 根窗口，两个弹框（选文件夹 / 选后缀＋密码＋放法）。
**只有这一个文件 import tkinter** —— 引擎和命令行都不碰界面。"""
import os
import sys
import tkinter as tk
from tkinter import filedialog

import engine
from engine import 规整后缀, 匹配后缀
from observe import 报, 去重

_根 = None          # 全程只开一个 Tk 根窗口，所有弹框都是它的 Toplevel
自检中 = False      # True 时弹框自己关掉（--诊断 用：证明「框真能建起来」）


def 弹框收尾(顶):
    """自检模式下让窗口 0.8 秒后自己关 —— 不用人点，也就不卡住自检。"""
    if 自检中:
        顶.after(800, 顶.destroy)




def 取根():
    """**全程只开一个 Tk 根窗口**（隐着不显示），所有弹框都是它的 Toplevel。
    ⚠️ 不能「每个框各开一个 tk.Tk()」—— 一个进程里连开三个根窗口是 Tk 的经典坑，
    实测会**卡死在那里不动**。改用单一根窗口就绕开了。"""
    global _根
    if _根 is None:
        import tkinter as tk
        _根 = tk.Tk()
        _根.withdraw()
    return _根




def 关根():
    global _根
    if _根 is not None:
        try:
            _根.destroy()
        except Exception:
            pass
        _根 = None




def 选文件夹():
    try:
        from tkinter import filedialog
        选 = filedialog.askdirectory(parent=取根(),
                                     title="选一个文件夹（里面的压缩包会被按内容认出来并解压；原文件不动）")
        报("【选文件夹】%s" % (选 or "（取消了）"))
        return 选 or None
    except Exception as e:
        # 不许静默兜底：弹不出来就把**真原因**打出来，别让人以为「这软件就是要你手输」
        报("（弹不出选择框：%s: %s）" % (type(e).__name__, e))
        报("（改手动输入路径；想看完整环境自检：加 --诊断）")
        try:
            return input("文件夹路径：").strip().strip('"') or None
        except EOFError:
            return None




def 选后缀和密码(根, 候选, 要选后缀=True):
    """**一个窗口**把两件事一起问完（把「自己填后缀」并进密码窗口）：
      ① 后缀：勾选＋自己补填（加在一起，不是替换），实时显示会处理几个文件
      ② 密码：当场敲（一行一个）
    返回 dict：{"后缀": [...], "密码": [...], "集中": bool}；
    取消 → None；没有 tkinter → None 但调用方按「没弹框、照命令行走」处理。"""
    from collections import Counter
    计数 = Counter()
    for f in 候选:
        计数[os.path.splitext(f)[1].lower()] += 1
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        报("（没 tkinter，弹框跳过 ⇒ 按命令行参数走）")
        return None

    结果 = {"后缀": None, "密码": [], "集中": None, "取消": False}
    顶 = tk.Toplevel(取根())
    顶.title("选后缀 ＋ 输密码")
    顶.geometry("640x800")
    顶.attributes("-topmost", True)
    tk.Label(顶, text="文件夹：%s" % 根, wraplength=610, justify="left").pack(fill="x", padx=10, pady=(8, 2))

    模式 = tk.IntVar(value=1 if engine.集中模式 else 0)
    甲0 = tk.LabelFrame(顶, text="① 放法（可切换）", padx=4, pady=2)
    甲0.pack(fill="x", padx=10, pady=(2, 4))
    tk.Radiobutton(甲0, text="每个源包一个目录（子文件夹名 = 来源）",
                   variable=模式, value=0, anchor="w").pack(fill="x")
    tk.Radiobutton(甲0, text="全部合并到一个文件夹（重名自动加 -1）",
                   variable=模式, value=1, anchor="w").pack(fill="x")

    甲 = tk.LabelFrame(顶, text="② 后缀 —— 勾的 ＋ 下面填的，都会被处理", padx=4, pady=4)
    甲.pack(fill="both", expand=True, padx=10, pady=(2, 6))

    外 = tk.Frame(甲)
    外.pack(fill="both", expand=True)
    布 = tk.Canvas(外, height=170, highlightthickness=0)
    条 = tk.Scrollbar(外, orient="vertical", command=布.yview)
    内 = tk.Frame(布)
    内.bind("<Configure>", lambda e: 布.configure(scrollregion=布.bbox("all")))
    布.create_window((0, 0), window=内, anchor="nw")
    布.configure(yscrollcommand=条.set)
    布.pack(side="left", fill="both", expand=True)
    条.pack(side="right", fill="y")

    勾 = []

    def 当前选的():
        """上面勾的 ＋ 下面填的 = **加在一起**（手填是「在上面基础上加」）。"""
        import re
        选 = [名 for 名, v in 勾 if v.get()]
        if 手填.get().strip():
            选 = 选 + 规整后缀(re.split(r"[,，\s、;；]+", 手填.get().strip()))
        return 去重(选)

    def 命中们():
        return [f for f in 候选 if 匹配后缀(f, 当前选的())]

    def 刷新计数(*_):
        n = len(命中们())
        计数标签.config(text=("会处理 %d 个文件" % n) if n else "⚠️ 当前一个文件都没命中",
                        fg="#2e7d32" if n else "#b00020")

    for 名, 数 in sorted(计数.items(), key=lambda kv: (-kv[1], kv[0])):
        v = tk.BooleanVar(value=True)
        tk.Checkbutton(内, text="%-14s （%d 个）" % (名 or "(无后缀)", 数),
                       variable=v, anchor="w", command=刷新计数).pack(fill="x")
        勾.append((名, v))

    def 全设(值):
        for _, v in 勾:
            v.set(值)
        刷新计数()

    可用 = "、".join("%s %d 个" % (k or "(无后缀)", v) for k, v in
                    sorted(计数.items(), key=lambda kv: (-kv[1], kv[0])))

    钮1 = tk.Frame(甲)
    钮1.pack(fill="x", pady=(4, 0))
    tk.Button(钮1, text="全选", command=lambda: 全设(True)).pack(side="left")
    tk.Button(钮1, text="全不选", command=lambda: 全设(False)).pack(side="left", padx=6)
    tk.Label(甲, text="↓ 也可以自己填后缀（逗号/空格分隔，例：mp3,jpg）——加在勾的上面：",
             fg="#37474f").pack(fill="x", pady=(4, 0))
    手填 = tk.Entry(甲)
    手填.pack(fill="x")
    手填.bind("<KeyRelease>", 刷新计数)
    计数标签 = tk.Label(甲, text="", fg="#2e7d32", anchor="w")
    计数标签.pack(fill="x", pady=(4, 0))
    tk.Label(甲, text="这个文件夹里实际有：" + 可用, fg="#607d8b",
             justify="left", wraplength=560, anchor="w").pack(fill="x")

    乙 = tk.LabelFrame(顶, text="③ 密码 —— 一行一个（当场敲，不保存）", padx=4, pady=4)
    乙.pack(fill="both", expand=True, padx=10, pady=(0, 4))
    tk.Label(乙, text="只活在这次运行里：不写盘、不打印、下次不保存。",
             fg="#37474f", anchor="w").pack(fill="x")
    框 = tk.Text(乙, height=6, wrap="none")
    框.pack(fill="both", expand=True)
    框.focus_set()

    提示 = tk.Label(顶, text="", fg="#b00020", justify="left", wraplength=610, anchor="w")
    提示.pack(fill="x", padx=10)

    def 定():
        if 要选后缀:
            选 = 当前选的()
            if not 选:
                报("【窗口】后缀一个都没勾、也没填 —— 至少选一个（没关窗）")
                提示.config(text="⚠️ 后缀一个都没勾、也没填 —— 至少选一个。")
                return
            命中 = 命中们()
            if not 命中:
                # 不许一声不吭地退：当场告诉他这个夹里到底有什么，**并且记进日志**
                报("【窗口】选/填的「%s」在这个夹里一个都没命中" % "、".join(选))
                报("【窗口】这个夹里实际有：%s" % 可用)
                提示.config(text="⚠️ 你选/填的 %s —— 一个都没命中。实际有的是：%s"
                                % ("、".join(选), 可用))
                return
            结果["后缀"] = 选
        结果["密码"] = [x.strip() for x in 框.get("1.0", "end").splitlines() if x.strip()]
        结果["集中"] = bool(模式.get())
        顶.destroy()

    def 消():
        结果["取消"] = True
        顶.destroy()

    顶.protocol("WM_DELETE_WINDOW", 消)          # 关窗 = 取消（别让它变成「什么都没发生」）
    钮 = tk.Frame(顶)
    钮.pack(fill="x", padx=10, pady=(4, 8))
    tk.Button(钮, text="取消", command=消).pack(side="right")
    tk.Button(钮, text="确定", command=定).pack(side="right", padx=6)
    if 要选后缀:
        刷新计数()
    else:
        甲.pack_forget()
        顶.geometry("620x430")
    弹框收尾(顶)
    顶.wait_window()
    if 结果["取消"]:
        报("【窗口】取消了")
        return None
    报("【窗口】后缀=%s ｜ 密码 %d 个（内容不记日志）"
       % ("、".join(结果["后缀"]) if 结果["后缀"] else "（按命令行给的）",
          len(结果["密码"])))
    return 结果




