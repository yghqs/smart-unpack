#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smart-unpack —— 批量解压「不认识后缀」的压缩包。

  · 按**内容**认压缩包（改过后缀也认得出），不看扩展名
  · 嵌套自动续解（包里还套着包），结果摊平：每个源包一个目录 / 全并到一个文件夹
  · 只读预检：密码对不上就跳过，不解压不复制
  · 断点友好：完成台账 + 半成品自愈；0 字节、卡死、zip slip 都有处置
  · 日志默认给路径打码（不外带使用者的目录结构）

模块：
  observe.py   日志 / 打码 / 计时（不碰界面，可单独用）
  engine.py    解压引擎（不碰界面，可单独 import）
  smart_unpack.py  本文件 = 界面 + 命令行入口

用法：
  python smart_unpack.py                 # 弹窗选文件夹
  python smart_unpack.py -d "<文件夹>"    # 指定文件夹
  python smart_unpack.py -d "..." --密码 "xxx" --集中
  python smart_unpack.py -d "..." --dry-run
"""
import argparse
import io
import os
import sys
import tempfile
import time

# ⚠️ 先把日志开起来再导入「要读配置」的模块：
#    配置坏了会抛错，若等到导入时才炸，那条错误就落不进日志（与「日志要自答」的规矩冲突）。
from observe import 日志, 报, 人读字节, 去重, 开日志, 关日志, 加日志落点, 日志文件名  # noqa: E402

开日志()

try:
    import config                                                            # noqa: E402
    import engine                                                            # noqa: E402
    import gui                                                               # noqa: E402
    import passwords                                                         # noqa: E402
    from gui import 选文件夹, 选后缀和密码, 取根, 关根
    from engine import (                       # 显式名单：用哪个引哪个（`import *` 是屎山的入口）
        超限, 有AES, 认头部,
        列文件, 规整后缀, 匹配后缀, 输出名, 出目录名,
        预检包, 解一个包, 再解一层,
        摊平搬移, 量目录, 不重名,
        挪走中间层, 删中间层,
        中间层清单, 中间层统计,
    )
except SystemExit as e:
    # 配置坏了会在导入期就抛（不等到 main）—— 那条错误也必须落进日志，不然「日志要自答」就是空话
    报("")
    报("💥 起不来：%s" % e)
    报("（这条也写进日志了：%s）" % 日志["路径"])
    raise


# 入口用到的几个配置（**正本在 config.py**：命令行 > 配置文件 > 默认值）
GB = 1024 ** 3
上限GB = engine.配置["上限GB"]
仅处理后缀 = config.仅处理后缀列表(engine.配置["仅处理后缀"])
递归子目录 = engine.配置["递归子目录"]
解压层数 = engine.配置["解压层数"]
上限层数 = engine.配置["上限层数"]
条目超时秒 = engine.配置["条目超时秒"]
集中模式 = engine.配置["集中模式"]

# 界面状态住在 gui.py（_根 / 自检中），这里不再复制一份

def main():
    # 配置与中间层状态住在 engine 里 —— 要**改 engine 的属性**，不是给本文件重新绑定一个同名变量
    # 输出**定成 UTF-8**（真控制台下本来就该是 UTF-8，等于无操作）；
    # 输出被重定向到管道/文件时，不这么做就会按本地码页（如 cp936）写 ——
    # 于是 ① 🛑 / ⚠️ 这种字符编不出，当场抛 UnicodeEncodeError 把程序打断；
    #      ② 抓输出的一方按 UTF-8 读，全是乱码。宁统一不猜。
    for 流 in (sys.stdout, sys.stderr):
        try:
            流.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="选文件夹 → 按内容认压缩包 → 直接解压（原文件不动；加密的当场给密码）")
    ap.add_argument("-d", "--目录", help="要处理的文件夹；不填就弹窗选")
    ap.add_argument("--全部", action="store_true", help="不弹选后缀的框，文件夹里所有文件都处理")
    ap.add_argument("--上限GB", type=float, default=上限GB, help="累计解出多少 GB 就停（默认 %.1f）" % 上限GB)
    ap.add_argument("--后缀", default=engine.输出后缀,
                    help="输出目录名按此后缀归一化（默认 %s）" % engine.输出后缀)
    ap.add_argument("--仅后缀", action="append", default=None,
                    help="只处理这些后缀的源文件，可多次；不填 = 全都处理"
                         "（命令行给了就**覆盖**配置文件里的，不是相加）")
    ap.add_argument("--密码", action="append", default=[], metavar="密码",
                    help="要用的密码（可给多次）。只对这次有效、不保存")
    ap.add_argument("--不弹密码框", action="store_true", help="不弹密码输入框（脚本化/无人值守用）")
    ap.add_argument("--集中", dest="集中", action="store_const", const=True, default=None,
                    help="集中模式：所有解出来的文件平铺到**一个文件夹**（重名自动加 -1）。"
                         "⚠️ 弹密码框时，界面里那道「放法」单选说了算（框里会**预选**你在这里"
                         "给的值，你还能改）；要它铁定生效，配 --不弹密码框")
    ap.add_argument("--分散", dest="集中", action="store_const", const=False,
                    help="分散模式：每个源包一个目录。⚠️ 弹密码框时，界面里那道「放法」单选"
                         "说了算（框里会**预选**你在这里给的值，你还能改）；要它铁定生效，"
                         "配 --不弹密码框")
    ap.add_argument("--全部重来", action="store_true",
                    help="无视「已解过就跳过」，全都重新解一遍")
    ap.add_argument("--超时秒", type=float, default=条目超时秒,
                    help="单条目**总耗时**上限，超了就放弃（默认 %.0f = 不限；慢≠坏，一般别设）"
                         % 条目超时秒)
    ap.add_argument("--停滞秒", type=float, default=engine.停滞秒,
                    help="连续这么多秒一个字节都写不出来才算卡死、放弃该条目（默认 %.0f）"
                         % engine.停滞秒)
    ap.add_argument("--输出", default=None, help="输出目录（默认 <文件夹>\\_解压输出）")
    ap.add_argument("--递归", dest="递归", action="store_const", const=True, default=None,
                    help="连子目录一起找（压过配置文件里的 递归子目录=false）")
    ap.add_argument("--不递归", dest="递归", action="store_const", const=False,
                    help="只处理这一层，不进子目录（同样压过配置文件）")
    ap.add_argument("--层数", type=int, default=解压层数,
                    help="解几层。0=自动（默认）：解出来的还是压缩包就接着解，最多 %d 层；"
                         "填 1 就只解外层" % 上限层数)
    ap.add_argument("--dry-run", action="store_true", help="只列清单，不解压")
    ap.add_argument("--诊断", action="store_true",
                    help="只打印环境自检（编码／是否已冻结／AES 库／tkinter 能不能起）后退出")
    ap.add_argument("--测试崩溃", action="store_true",
                    help="故意崩一次，用来验「崩溃留不留堆栈日志」")
    args = ap.parse_args()

    # 「命令行 > 配置文件 > 默认值」：命令行没给的，用配置文件/默认值补上
    if args.递归 is None:
        args.递归 = bool(递归子目录)

    if args.诊断:
        报("是否冻结(frozen)  ：%s" % getattr(sys, "frozen", False))
        报("stdout.encoding ：%s" % sys.stdout.encoding)
        报("pyzipper(AES)   ：%s" % ("在" if 有AES else "不在（AES 包解不了）"))
        try:
            import tkinter
            r = 取根()
            报("tkinter         ：能起（Tk %s）" % r.tk.call("info", "patchlevel"))
            # ⚠️ Tk 能起 ≠ 弹框能用：子模块没打进去时，Tk() 照样绿、askdirectory 当场炸
            for 子 in ("filedialog", "simpledialog", "messagebox", "ttk"):
                try:
                    __import__("tkinter." + 子)
                    报("tkinter.%-11s：在" % 子)
                except Exception as e2:
                    报("tkinter.%-11s：**不在** —— %s: %s" % (子, type(e2).__name__, e2))
        except Exception as e:
            报("tkinter         ：起不来 —— %s: %s" % (type(e).__name__, e))
        # Tk 能起 ≠ 我要用的那几个框能建起来 —— 建一遍，自检模式下它们自己关，不用人点
        报("---- 弹框自检（窗口会自己关，别管它）----")
        gui.自检中 = True
        假 = [os.path.join(os.getcwd(), "样a.mp3"), os.path.join(os.getcwd(), "样b.jpg")]
        for 名, kw in (("后缀＋密码窗口", dict(要选后缀=True)),
                       ("只密码窗口  ", dict(要选后缀=False))):
            try:
                r = 选后缀和密码(os.getcwd(), 假, **kw)
                报("%s：✅ 建起来了（自检返回 %s）" % (名, "取消" if r is None else "ok"))
            except Exception as e:
                报("%s：❌ 建不起来 —— %s: %s" % (名, type(e).__name__, e))
        gui.自检中 = False
        关根()
        return 0

    if args.测试崩溃:
        raise RuntimeError("这是专门用来试「崩了会不会留堆栈」的假崩溃，不是真故障")

    engine.输出后缀 = args.后缀

    根 = args.目录 or 选文件夹()
    if not 根:
        报("没选文件夹，退出。")
        return 1
    根 = os.path.abspath(根)
    if not os.path.isdir(根):
        报("不是文件夹：%s" % 根)
        return 1

    输出根 = os.path.abspath(args.输出) if args.输出 else os.path.join(根, "_解压输出")
    # 不复制副本：直接拿原文件读着解（只读，一个字节都不改）
    上限字节 = args.上限GB * GB

    报("=" * 62)
    报("文件夹：%s" % 根)
    报("输出到：%s" % 输出根)
    报("上限  ：%.2f GB（累计解出，触顶即全场停止）" % args.上限GB)
    报("输出后缀：%s    解压层数：%s    含子目录：%s"
       % (engine.输出后缀,
          "自动（还是压缩包就接着解，最多 %d 层）" % 上限层数 if args.层数 <= 0 else str(args.层数),
          "是" if args.递归 else "否"))
    # 把几个「时间闸」写在日志开头 —— 日志要自答，别让人去猜数字是多少
    报("时间闸：卡住 %.0f 秒没进展才放弃（--停滞秒）｜总耗时上限 %s（--超时秒，0=不限，慢≠坏）"
       "｜大文件每 %.0f 秒报一次心跳｜单条目超过 %.0f 秒单独记一行｜整包超过 %.0f 秒多打一行"
       % (args.停滞秒, "不限" if args.超时秒 <= 0 else "%.0f 秒" % args.超时秒,
          engine.心跳秒, engine.条目记账秒, engine.整包明细秒))
    报("=" * 62)

    候选 = [f for f in 列文件(根, args.递归)
            if not os.path.abspath(f).startswith(输出根 + os.sep)]
    if not 候选:
        报("这个文件夹里一个文件都没有" + ("（含子目录）" if args.递归 else "；子目录里可能有，加 --递归 试试"))
        return 0

    # 后缀和密码：命令行给了后缀（--仅后缀 / --全部）就按命令行；
    # 否则**一个窗口**里把「勾后缀 ＋ 输密码」一起问完（并成一个框）
    后缀们 = None
    if args.仅后缀:
        后缀们 = 规整后缀(args.仅后缀)          # 命令行显式给了 ⇒ 覆盖配置文件，不是相加
    elif args.全部:
        后缀们 = []
    elif 仅处理后缀:
        后缀们 = 规整后缀(仅处理后缀)          # 命令行没给 ⇒ 用配置文件里的
    给的密码 = passwords.从命令行(args.密码)
    if not args.不弹密码框:
        if args.集中 is not None:
            engine.集中模式 = args.集中      # 让界面**预选**命令行给的放法（用户还能改）
        r = 选后缀和密码(根, 候选, 要选后缀=(后缀们 is None))
        if r is None:
            报("")
            报("取消了，没动任何东西。")
            return 0
        if 后缀们 is None:
            后缀们 = r["后缀"]
        给的密码 = passwords.合并(给的密码, passwords.从界面(r["密码"]))
        if r.get("集中") is not None:
            args.集中 = bool(r["集中"])          # 界面上选的「放法」说了算
    if args.集中 is None:
        args.集中 = bool(集中模式)               # 命令行、界面都没给 ⇒ 用配置文件/默认值
    if 后缀们 is None:
        后缀们 = []
    后缀们 = 规整后缀(后缀们)          # 手填的 `mp4` / `MP4` 也认
    文件们 = [f for f in 候选 if 匹配后缀(f, 后缀们)]
    报("")
    报("要处理的后缀：%s" % ("、".join(s or "(无后缀)" for s in 后缀们) if 后缀们 else "（全部）"))
    if not 文件们:
        报("按所选后缀过滤后，没有可处理的文件。")
        if 后缀们:
            from collections import Counter
            计 = Counter(os.path.splitext(f)[1].lower() or "(无后缀)" for f in 候选)
            报("  你选的是      ：%s" % "、".join(后缀们))
            报("  这个夹里实际有：%s" % "、".join("%s %d 个" % (k, v) for k, v in 计.most_common()))
        return 0

    报("")
    报("【待处理清单】共 %d 个：" % len(文件们))
    for i, f in enumerate(文件们, 1):
        报("  %2d. %s  → %s" % (i, os.path.relpath(f, 根),
                              "并入输出根（集中模式）" if args.集中
                              else "解到 %s\\" % 出目录名(输出名(f))))

    if args.dry_run:
        报("")
        报("（--dry-run：到此为止，没解压）")
        return 0

    密码们 = passwords.合并(给的密码)
    if 密码们:
        报("")
        报("【密码】%d 个 —— 只对这次有效，不保存、不打印" % len(密码们))

    os.makedirs(输出根, exist_ok=True)
    # 工具自己写的东西（日志/报告/台账/中转站）**统统收进一个 `_记录\`** ——
    # 「把报告集中在一个文件夹里，不要太分散」（集中模式下否则会跟图混在一起）
    记录根 = os.path.join(输出根, "_记录")
    os.makedirs(记录根, exist_ok=True)
    engine.中间层根 = os.path.join(记录根, "_tmp")    # 中间过程的中转站，跑完就收走
    中间层根 = engine.中间层根        # 本地别名；引擎内部读的是 engine.中间层根（同一个值）
    加日志落点(os.path.join(记录根, 日志文件名()))
    # 「已解过就跳过」要跟「本轮刚建出来的」分清：先记下开跑前就已经在那儿的目录名
    # ⚠️ 「已解过」改成**正向记账**：只有**解完的**才写进台账。
    #    不能靠「看输出目录在不在」倒推 —— 中途被掐掉（关窗口/Ctrl-C）的目录**没有标记**，
    #    照样被当成「已解过」跳过 ⇒ 那个包永远解不完。
    # ⚠️ 还要记「**开跑前**就存在的目录名」：不然「同名包」的判定会出事 ——
    #    同一轮里第 1 个包刚建出 `要解压两次.mp3\`，第 2 个同名包就会把它当成「上次没解完的」**挪走删掉**。
    #    （后果举例：日志报 160 个文件、盘上只剩 83 个 —— 前面的结果被后面的删了。）
    开跑前已有 = {n.lower() for n in os.listdir(输出根)} if os.path.isdir(输出根) else set()
    台账路径 = os.path.join(记录根, "已完成.txt")
    已完成 = set()
    if os.path.isfile(台账路径):
        try:
            with io.open(台账路径, "r", encoding="utf-8") as f:
                已完成 = {行.strip() for 行 in f if 行.strip()}
        except OSError:
            已完成 = set()

    状态 = {"已解字节": 0, "上限字节": 上限字节, "条目数": 0, "命中": 0,
            "超时秒": args.超时秒, "停滞秒": args.停滞秒, "超时数": 0}
    计时总和 = {"秒": 0.0, "包数": 0}
    源合计 = 0                      # 解压目标：源包大小合计
    结果合计 = 0                    # 结果产物：**实测**最终目录大小合计
    结果文件合计 = 0                # 结果产物：文件数合计
    中间层统计["个数"] = 0          # 过程产物：每次跑清零
    中间层统计["字节"] = 0
    中间层清单.clear()
    行们 = []
    t0 = time.perf_counter()
    中断 = False
    try:
        for i, f in enumerate(文件们, 1):
            相对 = os.path.relpath(f, 根)
            包目录名 = 出目录名(输出名(f))        # 每个源包一个目录，**不带**源目录的日期层级
            集中 = args.集中
            台账键 = ("集中:" + 相对) if 集中 else 包目录名   # 集中模式没有「包目录」，用源文件相对路径当键
            报("")
            报("[%d/%d] %s%s" % (i, len(文件们), 相对, "   〔集中模式〕" if 集中 else ""))

            # 0 字节的源文件直接放弃 —— 多半是没下完的残file
            try:
                源大小 = os.path.getsize(f)
            except OSError:
                源大小 = -1
            if 源大小 == 0:
                报("        ⏭ 跳过 —— 源文件是 0 字节")
                行们.append((相对, 包目录名, "跳过：源文件 0 字节", 0))
                continue

            # 「已解过就跳过」：看**输出目录在不在**（选了的 ①）。
            # 用「开跑前就已有的目录名」判定，免得把本轮自己刚建出来的同名的也跳过。
            if not args.全部重来:
                旧 = os.path.join(输出根, 包目录名)
                # 台账记着、**但目录不在**（被删/被挪）⇒ 不算解过，重做。
                # （同名包互相删之后，台账还记着「解完了」，直接重跑会被全部跳过、永远补不回来）
                if 台账键 in 已完成 and (集中 or os.path.isdir(旧)):
                    报("        ⏭ 跳过 —— 台账里记着它上次解完了：%s" % (相对 if 集中 else 包目录名))
                    报("          要重解就加 --全部重来")
                    行们.append((相对, 包目录名, "跳过：台账里已完成", 0))
                    continue
                if 台账键 in 已完成 and not 集中 and not os.path.isdir(旧):
                    报("        ⚠️ 台账记着它解完了，可目录不在（被删/被挪）⇒ 当作没解过，重做：%s" % 包目录名)
                # 「挪走重做」只在分散模式有意义（集中模式没有包目录）
                if (not 集中) and 包目录名.lower() in 开跑前已有 and os.path.isdir(旧):
                    # **开跑前就在、台账里又没记** ⇒ 上次**没解完**（被掐掉/出错/旧版本留的）⇒ 挪走重做。
                    # 本轮自己刚建出来的同名目录**不在此列**（不在开跑前快照里），交给「不重名」去加 -1。
                    报("        ⚠️ 开跑前就有这个目录、台账里却没记（上次没解完）⇒ 挪走重做：%s" % 包目录名)
                    挪去 = 不重名(os.path.join(中间层根, 包目录名))
                    try:
                        os.makedirs(中间层根, exist_ok=True)
                        os.replace(旧, 挪去)              # 同盘改名，快
                        中间层清单.append(挪去)
                    except OSError as e:
                        报("        ⚠️ 旧目录挪不动（%s）—— 改成另起一个目录重做" % e)

            # ⚠️ 先**只读预检**再动手：密码对不上就直接跳过，不做任何多余的事（别浪费算力）
            t预检 = time.perf_counter()
            能, 说明, 命中密码 = 预检包(f, 密码们)
            秒预检 = time.perf_counter() - t预检
            报("        预检（只读）：%s   〔%.2f 秒〕" % (说明, 秒预检))
            if not 能:
                报("        ⏭ 跳过 —— 不解压")
                行们.append((相对, 包目录名, 说明 + "〔已跳过〕", 0))
                continue

            # 不再复制副本：**直接拿原文件读着解**（只读，一个字节都不改）。
            if 集中:
                # 集中模式：先解到 _tmp 下的**工作目录**，整个包全解完，再摊平倒进那个唯一的大文件夹。
                # （不能直接解进大文件夹 —— 否则「再解一层」会把**别的包**的文件也当成待解的扫一遍。）
                目标目录 = 不重名(os.path.join(中间层根 or tempfile.gettempdir(), "集中-" + 包目录名))
            else:
                目标目录 = 不重名(os.path.join(输出根, 包目录名))
            状态["本包未完成"] = False
            前总 = 状态["已解字节"]
            前中间 = 中间层统计["字节"]
            t解 = time.perf_counter()
            try:
                述, 本包 = 解一个包(f, 目标目录, 密码们, 状态,
                                    预设密码=命中密码, 预设说明=说明)
            except 超限:
                行们.append((相对, 包目录名, "触顶中断：本包只解了一部分", 0))
                raise
            秒解 = time.perf_counter() - t解
            条数 = 状态.get("条目数", 0) or 0
            # 多写一个「平均每条多少毫秒」：走走停停时，一眼能分出
            # 「每文件开销太大（杀软逐个扫）」还是「就那么慢」—— 日志要自答
            报("        → %s   〔%.2f 秒，%.1f MBps%s〕"
               % (述, 秒解, (本包 / 1048576.0 / 秒解) if 秒解 > 0 else 0,
                  "，共 %d 条 ⇒ 平均每条 %.0f 毫秒" % (条数, 秒解 * 1000.0 / 条数)
                  if 条数 > 1 else ""))
            行们.append((相对, 包目录名, 述, 本包))
            t内 = time.perf_counter()
            前总数 = 状态["已解字节"]
            try:
                再解一层(目标目录, (上限层数 if args.层数 <= 0 else args.层数) - 1,
                         密码们, 状态, 行们, 相对)
            except 超限:
                行们.append((相对, 包目录名, "触顶中断：内层没解完", 0))
                raise
            if 集中:
                # 集中模式：整个包全解完了 ⇒ 把工作目录里的成品**摊平**倒进那个唯一的大文件夹（重名加 -1）
                搬字节, 搬个数 = 摊平搬移(目标目录, 输出根)
                中间层清单.append(目标目录)        # 搬空了的壳，跟其他中间产物一起收走
            删中间层()                      # 这个包解完了 ⇒ 它的中间层立刻收走（不攒着占地方）
            # 正向记账：**只在真解完时记一笔**（半成品不记 —— 下次才会重做它）
            if not 状态.get("本包未完成"):
                try:
                    with io.open(台账路径, "a", encoding="utf-8") as f:
                        f.write(台账键 + "\n")
                except OSError as e:
                    报("        ⚠️ 台账写不进（%s）—— 下次可能重做这个包" % e)
            秒内 = time.perf_counter() - t内
            内层量 = 状态["已解字节"] - 前总数
            if 内层量:
                报("        ↳ 内层 〔%.2f 秒，%.1f MBps〕"
                   % (秒内, (内层量 / 1048576.0 / 秒内) if 秒内 > 0 else 0))
            总计 = 秒预检 + 秒解 + 秒内
            # ⚠️ 结果产物**从最终目录实测**，不能用「已解字节」累计 ——
            #    累计里含**中间层压缩包**（它随后被收走删掉），会把结果虚报成近两倍
            if 集中:
                本包结果, 结果文件数 = 搬字节, 搬个数
            else:
                本包结果, 结果文件数 = 量目录(目标目录)
            本包中间 = 中间层统计["字节"] - 前中间
            源合计 += max(0, 源大小)
            结果文件合计 += 结果文件数
            结果合计 += 本包结果
            报("        ⏱ 本包合计 %.2f 秒（预检 %.2f ＋ 外层 %.2f ＋ 内层 %.2f）"
               "｜ 源 %s ⇒ 结果 %s（%d 个文件）｜ 过程产物 %s"
               % (总计, 秒预检, 秒解, 秒内, 人读字节(max(0, 源大小)),
                  人读字节(本包结果), 结果文件数, 人读字节(本包中间)))
            if 总计 >= engine.整包明细秒:
                报("        ⏱（其中 %.0f%% 花在外层）" % (秒解 / 总计 * 100))
            计时总和["秒"] += 总计
            计时总和["包数"] += 1
    except 超限:
        中断 = True
        报("")
        报("🛑 累计解出已达上限 %.2f GB —— 全场停止。" % args.上限GB)
        报("   已解出合计：%s" % 人读字节(状态["已解字节"]))
        报("   未完成的那份已留 _未完成.txt 标记，没删任何东西。")

    用时 = time.perf_counter() - t0
    报告路径 = os.path.join(记录根, "解压报告.txt")
    with io.open(报告路径, "w", encoding="utf-8") as rp:
        rp.write("批量解压报告 · %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        rp.write("（本文件含**真实路径**，只给你本机自己看；要外发请先自行处理）\n")
        rp.write("源文件夹：%s\n" % 根)
        rp.write("上限：%.2f GB   实际解出：%s   用时：%.1f 秒%s\n"
                 % (args.上限GB, 人读字节(状态["已解字节"]), 用时, "   【触顶中断】" if 中断 else ""))
        rp.write("（密码内容一律不记录，只记「第 N 个」）\n\n")
        rp.write("放法：%s\n\n" % ("全部合并到输出根（集中模式）" if args.集中
                                else "每个源包一个目录（分散模式）"))
        for 序, (源, 副, 述, 字节) in enumerate(行们, 1):
            if args.集中:
                rp.write("%d. %s\n   落到：输出根下平铺\n   结果：%s\n   解出：%s\n"
                         % (序, 源, 述, 人读字节(字节)))
            else:
                rp.write("%d. %s\n   输出目录：%s\\\n   结果：%s\n   解出：%s\n"
                         % (序, 源, 副, 述, 人读字节(字节)))

    报("")
    报("=" * 62)
    报("完成。处理 %d 个，成功解出 %s，用时 %.1f 秒" % (len(行们), 人读字节(状态["已解字节"]), 用时))
    if 状态.get("超时数"):
        报("⚠️ 有 %d 个条目超时被放弃（半成品都改名成 .超时未完成 了）" % 状态["超时数"])
    报("📦 尺寸：解压目标（源包）%s ｜ 结果产物 %s（%d 个文件，实测）｜ 过程产物 %s（%d 个，已收走）"
       % (人读字节(源合计), 人读字节(结果合计), 结果文件合计,
          人读字节(中间层统计["字节"]), 中间层统计["个数"]))
    报("⏱ 计时：真解了 %d 个包，加起来 %.1f 秒（%.1f MBps）；"
       "其余 %.1f 秒是清单、跳过、收尾等"
       % (计时总和["包数"], 计时总和["秒"],
          (状态["已解字节"] / 1048576.0 / 计时总和["秒"]) if 计时总和["秒"] > 0 else 0,
          max(0.0, 用时 - 计时总和["秒"])))
    报("报告：%s" % 报告路径)
    报("输出：%s" % 输出根)
    报("（原文件一个字节没动 —— 全程只读；%s）"
       % ("结果全部平铺在上面那个输出目录里" if args.集中
          else "结果每个源包一个目录，都在上面那个输出目录里"))
    报("=" * 62)
    return 0



def 脱敏参数(argv):
    """把命令行里的密码值打码再进日志 —— 日志是给查错看的，不该躺着密码。"""
    出 = []
    跳 = False
    for a in argv:
        if 跳:
            跳 = False
            continue
        if a == "--密码":
            出.append(a)
            出.append("***")
            跳 = True
        elif a.startswith("--密码="):
            出.append("--密码=***")
        else:
            出.append(a)
    return " ".join(出)



if __name__ == "__main__":
    开日志()
    报("")
    报("=" * 62)
    报("smart-unpack 启动 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    报("是否 exe ：%s ｜ 参数：%s" % (getattr(sys, "frozen", False), 脱敏参数(sys.argv[1:])))
    报("日志落点 ：%s" % 日志["路径"])
    码 = 1
    try:
        码 = main()
    except SystemExit as e:
        码 = e.code if isinstance(e.code, int) else 0
    except BaseException:
        import traceback
        报("")
        报("💥 崩了 —— 完整堆栈（这段就是拿来查错的）：")
        报(traceback.format_exc())
        报("把上面这几行连同日志一起留档，即可定位。")
        码 = 1
    finally:
        报("")
        报("—— 本次结束，退出码 %s ｜ 完整日志：%s" % (码, 日志["路径"]))
    # 双击 exe 时窗口一闪就关 = 什么都看不见（例：后缀填错 ⇒ 一行就退 ⇒ 看着像没动静）。
    # 有真控制台就停一下等人按键；从命令行/管道跑时 stdin 不是终端，不拦，免得脚本卡死。
    if getattr(sys, "frozen", False) and sys.stdin and sys.stdin.isatty():
        try:
            input("\n（按回车关闭窗口）")
        except EOFError:
            pass
    关根()
    关日志()
    sys.exit(码)

