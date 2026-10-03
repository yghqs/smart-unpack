#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
zip解压助手 · 自测（阳／阴对照）

阳（应当成功）：
  1 普通未加密 zip（含中文名、含子目录）        → 解出，内容对
  2 ZipCrypto 加密 zip（第 2 个密码对得上）      → 解出
  3 AES 加密 zip（pyzipper 造）                  → 命中
  4 假 zip（其实是个文本文件）                   → 明确报「不是有效的 ZIP」，不崩
  7 不给密码：加密的明说「需要密码，但没提供密码」，**不加密的照样解**
  8 给的密码全不对：报「都不对」且不崩
  9 后缀过滤：只勾/只填某些后缀 ⇒ 没选中的**碰都不碰**（连输出目录都不建）
 10 嵌套解压：层数 2 解到第二层；层数 1 只解外层、不碰内层
 11 手写后缀：`mp3`（不带点）认；`.MP4`（不匹配）一个都不处理
阴（应当挡住）：
  5 zip slip：条目名带 ../逃出去.txt            → 目标目录之外**不许**出现该文件
  6 上限熔断：小上限 + 大文件                    → 报告里出现「触顶中断」，且没解过头

用法： python selftest.py
产物落在系统的临时目录下，**每轮开一个新目录**。
"""
import glob
import io
import os
import re
import struct
import zlib
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 默认测源码；设 SMART_UNPACK_TOOL=<exe 路径> 就把同一套断言原封不动打到 exe 上
TOOL = os.environ.get("SMART_UNPACK_TOOL") or os.path.join(HERE, "smart_unpack.py")
BASE = os.path.join(os.path.dirname(HERE), "_tmp", "ziptest")
右 = 0
错 = 0
戳 = time.strftime("%m%d-%H%M%S")


def 找7z():
    for c in (os.path.join(HERE, "bin", "7z.exe"), os.path.join(HERE, "bin", "7z"),
              r"C:\Program Files\7-Zip\7z.exe"):
        if os.path.isfile(c):
            return c
    return None


def 判(名, 条件, 说明=""):
    global 右, 错
    if 条件:
        右 += 1
        print("  ✅ %s" % 名)
    else:
        错 += 1
        print("  ❌ %s   %s" % (名, 说明))


def 出目录名(副名, 后缀=".zip"):
    if 副名.lower().endswith(后缀.lower()):
        基 = 副名[:len(副名) - len(后缀)]
        return 基 or 副名
    return 副名


def 读(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def 建素材(FIX, SRC):
    os.makedirs(FIX, exist_ok=True)
    os.makedirs(SRC, exist_ok=True)

    with zipfile.ZipFile(os.path.join(FIX, "普通包.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("中文说明.txt", "这是中文内容，用于验编码。")
        z.writestr("子目录/里面.txt", "嵌套一层。")

    if 找7z():
        源 = os.path.join(SRC, "七个字.txt")
        with io.open(源, "w", encoding="utf-8") as f:
            f.write("ZipCrypto 内容。")
        # cwd 设到源目录，让压缩包里的条目名是纯文件名，不带路径
        subprocess.run([找7z(), "a", "-tzip", "-phunter2", "-mem=ZipCrypto",
                        os.path.join(FIX, "旧式加密包.rar"), "七个字.txt"],
                       cwd=SRC, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    # ---- 真 7z / 真 RAR（2026-10-04 加：本工具现在**也解 7z 与 RAR**）----
    if 找7z():
        七 = 找7z()
        源7 = os.path.join(SRC, "7z源")
        os.makedirs(os.path.join(源7, "子"), exist_ok=True)
        with io.open(os.path.join(源7, "中文名.txt"), "w", encoding="utf-8") as f:
            f.write("7z 里的中文内容。")
        with io.open(os.path.join(源7, "子", "里面.txt"), "w", encoding="utf-8") as f:
            f.write("7z 子目录里的内容。")
        # ① 无加密 7z —— **后缀故意乱写**（.mp3）：必须靠**内容**认出来，不看后缀
        subprocess.run([七, "a", "-t7z", os.path.join(FIX, "真7z包.mp3"), "中文名.txt", "子"],
                       cwd=源7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        # ② 加密 7z（密码 hunter2）
        subprocess.run([七, "a", "-t7z", "-phunter2", os.path.join(FIX, "加密7z包.dat"), "中文名.txt"],
                       cwd=源7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        # ③ **连文件名都加密**（-mhe=on）—— 最容易答错的一种：连列目录都要密码，得**换密码再列一次**
        subprocess.run([七, "a", "-t7z", "-phunter2", "-mhe=on",
                        os.path.join(FIX, "加密头7z包.dat"), "中文名.txt"],
                       cwd=源7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    # ④ 真 RAR：7z **只会解、不会造**（实测 `a -trar` 报「未实现」）⇒ 样本得随仓库带。
    #    `testdata\` 里那两个 .rar 是**我们自己造的最小样本**（几百字节，内容是三行自己写的字）：
    #    用 WinRAR 的 `Rar.exe` 生成 —— ⚠️ **只带生成的 .rar，不带 `Rar.exe` 本身**（共享软件）。
    #    两代都要：`engine.是7z或rar()` 判 RAR4 / RAR5 是**两条魔数分支**（差第 7 个字节）。
    RAR样本 = os.path.join(HERE, "testdata", "真RAR样本.rar")
    if os.path.isfile(RAR样本):
        shutil.copy2(RAR样本, os.path.join(FIX, "真RAR包.dat"))
    RAR4样本 = os.path.join(HERE, "testdata", "真RAR4样本.rar")
    if os.path.isfile(RAR4样本):
        shutil.copy2(RAR4样本, os.path.join(FIX, "真RAR4包.dat"))

    try:
        import pyzipper
        with pyzipper.AESZipFile(os.path.join(FIX, "AES加密包.dat"), "w",
                                 compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(b"hunter2")
            z.writestr("aes内容.txt", "AES 加密的内容。")
    except ImportError:
        print("  （没装 pyzipper，跳过 AES 素材）")

    with io.open(os.path.join(FIX, "假包.bin"), "w", encoding="utf-8") as f:
        f.write("我根本不是压缩包，只是改了个名字。\n" * 5)

    # ⑥ 假 xlsx：**就是个 zip**，但带 OOXML 的标志文件 ⇒ 工具应当**故意不拆**
    with zipfile.ZipFile(os.path.join(FIX, "看着像表格.xlsx"), "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    # ⑦ **真表格**套在一个 zip 里：验「内层的文档也不拆」
    缓表 = io.BytesIO()
    with zipfile.ZipFile(缓表, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    with zipfile.ZipFile(os.path.join(FIX, "里面有表格的包.zip"), "w") as z:
        z.writestr("内层表格.xlsx", 缓表.getvalue())
        z.writestr("普通.txt", "我是普通文件。")
    # ⑧ 加密 7z，**唯一条目是 0 字节文件** ⇒ 挑不出探针
    if 找7z():
        open(os.path.join(源7, "空条目.txt"), "wb").close()
        subprocess.run([找7z(), "a", "-t7z", "-phunter2",
                        os.path.join(FIX, "加密空条目7z.dat"), "空条目.txt"],
                       cwd=源7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    # ⑨ 「其实是 zip 的交付物」三件 —— 两种判据各要一个能验到的
    with zipfile.ZipFile(os.path.join(FIX, "程序.jar"), "w") as z:        # 判据=后缀
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.writestr("com/x/Main.class", "字节码")
    with zipfile.ZipFile(os.path.join(FIX, "作品.sb3"), "w") as z:        # 判据=**读 project.json 内容**
        z.writestr("project.json", '{"targets":[],"monitors":[]}')        # ← 有 targets 才算 Scratch
        z.writestr("造型1.svg", "<svg/>")
    with zipfile.ZipFile(os.path.join(FIX, "改名包.mp3"), "w") as z:      # 判据=**内容**（后缀骗人）
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")     # ← jar 的标志，只是改了名
        z.writestr("com/x/Main.class", "字节码")
    # ⑬ **名字像交付物、内容其实是普通分享包** ⇒ **必须照拆**（真事：上次运行时 14 个正常分享包
    #    被「后缀名单」整条挡掉 ⇒ 名单默认已改空）
    with zipfile.ZipFile(os.path.join(FIX, "名字像表格.xlsx"), "w") as z:
        z.writestr("分享说明.txt", "我内容就是普通分享包，名字起得像 xlsx 而已。")
        z.writestr("图.png", "假装是张图")
    # ⑭ **只有一个通用名文件、没有第二条证据** ⇒ 也该**照拆**（`META-INF/MANIFEST.MF`
    #    在很多普通 zip 里都有，一个就定性太宽）
    with zipfile.ZipFile(os.path.join(FIX, "带个通用名的包.zip"), "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")   # 只有它，**没有 .class**
        z.writestr("真正的分享内容.txt", "我才是这个包的正经内容。")

    # ⑩ **5 层套娃**：每层「原样存（ZIP_STORED）内层包」⇒ 内层包按**原尺寸**写出。
    #    改之前额度会**按层数翻倍扣**（实测 5 层 ×3~5 倍）。
    内 = None
    for i in range(5, 0, -1):
        p = os.path.join(SRC, "套%d.zip" % i)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
            z.writestr("第%d层数据.bin" % i, b"B" * (300 * 1024))
            if 内:
                z.write(内, "里面还有.zip")
        内 = p
    if os.path.isfile(os.path.join(SRC, "套1.zip")):
        shutil.copy2(os.path.join(SRC, "套1.zip"), os.path.join(FIX, "五层套.zip"))

    # ⑪ **图种**：PNG 头 + 7z 身子。7z/RAR 的标志在**文件头**，前面垫张图就不在头上了
    #    ⇒ 只读头 8 字节的实现会整条漏掉（而 7-Zip 自己扫全文件、认得出来）。
    #    注意 **PNG+zip** 那种侥幸没事（zip 认尾部 EOCD）—— 所以这里**必须用 7z** 才验得到。
    def _小PNG():
        def 块(t, d):
            return (struct.pack(">I", len(d)) + t + d
                    + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
        像素 = b"".join(b"\x00" + (b"\x28\x6e\xb4" * 8) for _ in range(8))
        return (b"\x89PNG\r\n\x1a\n" + 块(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                + 块(b"IDAT", zlib.compress(像素, 9)) + 块(b"IEND", b""))

    if 找7z():
        源种 = os.path.join(SRC, "种")
        os.makedirs(源种, exist_ok=True)
        with io.open(os.path.join(源种, "图种里的.txt"), "w", encoding="utf-8") as f:
            f.write("我是藏在 PNG 后面的内容。")
        subprocess.run([找7z(), "a", "-t7z", os.path.join(源种, "身子.7z"), "图种里的.txt"],
                       cwd=源种, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        身 = os.path.join(源种, "身子.7z")
        if os.path.isfile(身):
            with open(os.path.join(FIX, "图种7z.png"), "wb") as f:
                f.write(_小PNG() + io.open(身, "rb").read())

    with zipfile.ZipFile(os.path.join(FIX, "越界包.zip"), "w") as z:
        z.writestr("../逃出去.txt", "我不该出现在目标目录外面。")
        z.writestr("正常.txt", "我是正常的。")


def 跑(目录, 密码们, 上限GB, 输出, 仅后缀=None, 层数=None, 一次密码=None, 其他=None):
    """`密码们` 是**一个列表**（不再是从文件读出来的那些行）。

    ⚠️ **这里 deliberately 不是「读一个密码文件」** —— 本工具对外只有一条密码通路：
       **手给 `--密码`**。之前自测里有个「读 txt → 逐行喂 --密码」的包装，
       那等价于在仓库里留了一条「**从文件读密码本**」的通路，与「不从字典炮轰」的对外立场相冲；
       测试自己也**不需要**那条路（写死几个假口令就够了）⇒ 改成**内联列表**，通路消失。"""
    基 = [TOOL] if TOOL.lower().endswith(".exe") else [sys.executable, TOOL]
    cmd = 基 + ["-d", 目录, "--上限GB", str(上限GB), "--输出", 输出]
    cmd += ["--全部"] if 仅后缀 is None else ["--仅后缀", 仅后缀]
    cmd += ["--不弹密码框"]          # 自测不能弹框等人敲
    if 层数 is not None:
        cmd += ["--层数", str(层数)]
    for p in (密码们 or []):
        cmd += ["--密码", p]
    for p in (一次密码 or []):
        cmd += ["--密码", p]
    for a in (其他 or []):
        cmd.append(a)
    环 = dict(os.environ)
    环["PYTHONIOENCODING"] = "utf-8"     # 不设的话，被冻结的 exe 会按本地码页(GBK)写管道
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=环)


def 主():
    FIX = os.path.join(BASE, "fixtures-" + 戳)
    SRC = os.path.join(BASE, "src-" + 戳)
    BIG = os.path.join(BASE, "big-" + 戳)
    # ⚠️ 密码候选**内联写死**，不落成密码文件 —— 见 跑() 的说明
    BOOK = ["错密码", "hunter2", "再来一条"]
    坏本 = ["不是这个", "也不是这个"]
    os.makedirs(BASE, exist_ok=True)
    建素材(FIX, SRC)

    print("=" * 62)
    print("自测开始 · 素材 %s" % FIX)
    print("=" * 62)

    输出 = os.path.join(BASE, "out-" + 戳)
    甲 = os.path.join(输出, "A")
    p = 跑(FIX, BOOK, 5.0, 甲)
    文 = p.stdout + p.stderr
    print(p.stdout)

    判("退出码 0", p.returncode == 0, "returncode=%s" % p.returncode)
    判("清单列出 ≥4 个待处理", 文.count("  → 解到 ") >= 4, "实际 %d" % 文.count("  → 解到 "))

    # 1 普通包
    判("普通包·中文名解出且内容对",
       os.path.isfile(os.path.join(甲, "普通包", "中文说明.txt")) and
       读(os.path.join(甲, "普通包", "中文说明.txt")).startswith("这是中文内容"))
    判("普通包·压缩包里的子目录被摊平（只嵌一层）",
       os.path.isfile(os.path.join(甲, "普通包", "里面.txt")))

    # 2 ZipCrypto
    if 找7z():
        判("旧式加密包·第 2 个密码对上了", "第 2 个密码对上了" in 文)
        判("旧式加密包（ZipCrypto）·走 7z 引擎，不用纯 Python 解",
           "走 7z 引擎" in 文, [l for l in 文.splitlines() if "ZipCrypto" in l][:1])
        判("旧式加密包·内容解出",
           os.path.isfile(os.path.join(甲, 出目录名("旧式加密包.rar.zip"), "七个字.txt")))
    else:
        print("  （没 7z，跳过 ZipCrypto 两条断言）")

    # 3 AES
    if os.path.exists(os.path.join(FIX, "AES加密包.dat")):
        判("AES 包·内容解出",
           os.path.isfile(os.path.join(甲, 出目录名("AES加密包.dat.zip"), "aes内容.txt")))

    # 4 假包
    判("假包·报「不是有效的 ZIP」", "不是有效的 ZIP" in 文)

    # 5 zip slip —— 上下两层都查
    判("zip slip·目标目录外没冒出文件",
       not os.path.exists(os.path.join(输出, "逃出去.txt")) and
       not os.path.exists(os.path.join(甲, "逃出去.txt")))
    判("zip slip·包内正常条目仍解出",
       os.path.isfile(os.path.join(甲, "越界包", "正常.txt")))

    # 6 熔断
    os.makedirs(BIG, exist_ok=True)
    with zipfile.ZipFile(os.path.join(BIG, "大文件.zip"), "w", zipfile.ZIP_STORED) as z:
        z.writestr("大块.bin", b"A" * (8 * 1024 * 1024))
    乙 = os.path.join(输出, "B")
    p2 = 跑(BIG, None, 0.001, 乙)                 # 上限 ≈ 1 MB
    文2 = p2.stdout + p2.stderr
    print(p2.stdout[-1000:])
    判("熔断·控制台说全场停止", "全场停止" in 文2)
    报告 = os.path.join(乙, "_记录", "解压报告.txt")
    判("熔断·报告标「触顶中断」", os.path.isfile(报告) and "触顶中断" in 读(报告))
    已解 = 0
    for r, _, fs in os.walk(os.path.join(乙, "大文件")):
        for f in fs:
            已解 += os.path.getsize(os.path.join(r, f))
    判("熔断·落盘没超过上限", 已解 <= 0.001 * 1024 ** 3,
       "实际落盘 %d 字节" % 已解)
    判("熔断·未完成目录留了标记",
       os.path.exists(os.path.join(乙, "大文件", "_未完成.txt")))

    # 7 不给任何密码：加密的要明说、不加密的照解
    print("-" * 62)
    丙 = os.path.join(输出, "C")
    源样 = os.path.join(FIX, "普通包.zip")
    跑前 = (os.path.getsize(源样), os.path.getmtime(源样))
    p3 = 跑(FIX, None, 5.0, 丙)
    文3 = p3.stdout + p3.stderr
    判("没给密码·加密包明说「需要密码，但没提供密码」", "需要密码，但没提供密码" in 文3)
    判("无密码·不加密的包照样解出",
       os.path.isfile(os.path.join(丙, "普通包", "中文说明.txt")))
    判("省算力·密码对不上就一点没动（预检拦住，连输出目录都没建）",
       not os.path.exists(os.path.join(丙, 出目录名("旧式加密包.rar.zip"))))
    # 「原文件不动」要**拿前后实测去撞**，不能断言一个已经不存在的目录（那样的断言恒真、等于没验）
    跑后 = (os.path.getsize(源样), os.path.getmtime(源样))
    判("全程只读·源文件大小与修改时间一个都没变", 跑前 == 跑后, "%s → %s" % (跑前, 跑后))
    判("省算力·报里说明了预检结论", "预检（只读）" in 文3)

    # 8 给的密码全不对
    丁 = os.path.join(输出, "D")
    p4 = 跑(FIX, 坏本, 5.0, 丁)
    文4 = p4.stdout + p4.stderr
    判("密码全不对·报「都不对」且不崩", "都不对" in 文4 and p4.returncode == 0)

    # 9 后缀过滤：只勾 .zip ⇒ 只处理 2 个（普通包、越界包），别的一律不碰
    print("-" * 62)
    戊 = os.path.join(输出, "E")
    p5 = 跑(FIX, BOOK, 5.0, 戊, 仅后缀=".zip")
    文5 = p5.stdout + p5.stderr
    # ⚠️ 别把「几个」写死 —— 加一个素材这条就假红。改成断**不变式**：勾了 .zip 就**只许出现 zip**。
    列了5 = [l for l in 文5.splitlines() if "  → 解到 " in l]
    判("后缀过滤·只勾 .zip ⇒ **只列 zip**（不碰别的）",
       bool(列了5) and all(".zip" in l for l in 列了5), 列了5[:4])
    判("后缀过滤·勾中的解出", os.path.isfile(os.path.join(戊, "普通包", "中文说明.txt")))
    判("后缀过滤·没勾的没被碰",
       not os.path.exists(os.path.join(戊, 出目录名("旧式加密包.rar.zip"))) and
       not os.path.exists(os.path.join(戊, 出目录名("假包.bin.zip"))))
    判("后缀过滤·没勾的一点没动", not os.path.exists(os.path.join(戊, "假包.bin")))

    # 10 嵌套（「要解压两次」那种）：层数 2 要解到第二层；层数 1 只解外层
    print("-" * 62)
    NEST = os.path.join(BASE, "nest-" + 戳)
    os.makedirs(NEST, exist_ok=True)
    缓 = io.BytesIO()
    with zipfile.ZipFile(缓, "w") as z:
        z.writestr("里面.txt", "我是第二层的内容。")
    with zipfile.ZipFile(os.path.join(NEST, "套娃.mp3"), "w") as z:
        z.writestr("内层.bin", 缓.getvalue())      # 内层也是压缩包，只是名字不像
    outer = 出目录名("套娃.mp3.zip")
    己 = os.path.join(输出, "F")
    p6 = 跑(NEST, None, 5.0, 己, 层数=2)
    判("嵌套·层数2 解到第二层（摊平在包目录里）",
       os.path.isfile(os.path.join(己, outer, "里面.txt")))
    判("嵌套·层数2 包目录里只留内容（中间层不在里面）",
       not os.path.exists(os.path.join(己, outer, "内层.bin")))
    巢 = os.path.join(己, "_记录", "_tmp")
    判("嵌套·中间层自动删除（_tmp 收干净了）",
       not os.path.isdir(巢) or not os.listdir(巢),
       巢 if os.path.isdir(巢) else "")
    庚 = os.path.join(输出, "G")
    跑(NEST, None, 5.0, 庚, 层数=1)
    判("嵌套·层数1 只解外层（内层包还在包目录里）",
       os.path.isfile(os.path.join(庚, outer, "内层.bin")))
    判("嵌套·层数1 不碰内层内容", not os.path.exists(os.path.join(庚, outer, "里面.txt")))

    # 11 手写后缀不带点也要认（使用者会写 `mp4` 而不是 `.mp4`）
    print("-" * 62)
    辛 = os.path.join(输出, "H")
    p7 = 跑(NEST, None, 5.0, 辛, 仅后缀="mp3", 层数=1)
    文7 = p7.stdout + p7.stderr
    判("手填后缀 mp3（不带点）·认出来了", 文7.count("  → 解到 ") == 1, "实际 %d" % 文7.count("  → 解到 "))
    壬 = os.path.join(输出, "I")
    p8 = 跑(NEST, None, 5.0, 壬, 仅后缀=".MP4", 层数=1)
    文8 = p8.stdout + p8.stderr
    判("手填后缀 MP4（大写、不匹配）·一个都不处理", "没有可处理的文件" in 文8)
    判("选错后缀·列出这个夹里实际有的（不许一声不吭）",
       "你选的是" in 文8 and ".mp3 1 个" in 文8)

    # 12 一次性密码：不预置任何密码，只当场给 ⇒ 照样命中；且**不许把密码原文打出来**
    print("-" * 62)
    癸 = os.path.join(输出, "J")
    p9 = 跑(FIX, None, 5.0, 癸, 一次密码=["错密码", "hunter2", "再来一条"])
    文9 = p9.stdout + p9.stderr
    判("多个密码·第 2 个对上了", "第 2 个密码对上了" in 文9)
    判("多个密码·输出里没有密码原文", "hunter2" not in 文9)
    判("多个密码·AES 包也解出来了",
       os.path.isfile(os.path.join(癸, 出目录名("AES加密包.dat.zip"), "aes内容.txt")))

    # 13 崩了必须留堆栈 —— 「不加日志都找不到错」
    print("-" * 62)
    找崩溃 = glob.glob(os.path.join(os.path.dirname(os.path.abspath(TOOL)), "smart-unpack日志*.txt"))
    日志路径 = max(找崩溃, key=os.path.getmtime) if 找崩溃 else os.path.join(   # 取**最新**那份
        os.path.dirname(os.path.abspath(TOOL)), "smart-unpack日志.txt")
    p10 = 跑(FIX, None, 5.0, os.path.join(输出, "K"), 其他=["--测试崩溃"])
    文10 = p10.stdout + p10.stderr
    判("崩溃·退出码是 1", p10.returncode == 1, "实际 %s" % p10.returncode)
    判("崩溃·屏幕上打出 Traceback", "Traceback" in 文10)
    判("崩溃·日志文件存在", os.path.isfile(日志路径), 日志路径)
    if os.path.isfile(日志路径):
        with io.open(日志路径, encoding="utf-8", errors="replace") as f:
            尾巴 = f.read()[-5000:]
        判("崩溃·堆栈写进了日志", "Traceback" in 尾巴 and "假崩溃" in 尾巴)
        判("崩溃·日志记了退出码", "本次结束，退出码 1" in 尾巴)

    # 14 「已解过就跳过」（选了的 ①：看输出目录在不在）
    print("-" * 62)
    子 = os.path.join(输出, "L")
    p11 = 跑(FIX, BOOK, 5.0, 子)
    文11a = p11.stdout + p11.stderr
    p12 = 跑(FIX, BOOK, 5.0, 子)
    文11b = p12.stdout + p12.stderr
    判("首跑·真解了（≥3 个包出了「本包合计」）", 文11a.count("⏱ 本包合计") >= 3,
       "实际 %d" % 文11a.count("⏱ 本包合计"))
    判("重跑·跳过已解出的（台账里记着）", "台账里记着它上次解完了" in 文11b)
    判("重跑·一个包都没真解（全跳过了）", 文11b.count("⏱ 本包合计") == 0,
       "实际 %d" % 文11b.count("⏱ 本包合计"))
    p13 = 跑(FIX, BOOK, 5.0, 子, 其他=["--全部重来"])
    文11c = p13.stdout + p13.stderr
    判("--全部重来·不跳了、照旧解",
       "台账里记着它上次解完了" not in 文11c and 文11c.count("⏱ 本包合计") >= 3)
    判("台账·解完了就记一笔（_记录\\已完成.txt）",
       os.path.isfile(os.path.join(子, "_记录", "已完成.txt")))

    # 15 --诊断 的弹框自检：**两个框必须真能建起来**（这条抓的是「选后缀 NameError」那类
    #     —— 框建不起来的 bug 藏在 GUI 里，不这么测根本看不见）
    print("-" * 62)
    p14 = 跑(FIX, None, 5.0, os.path.join(输出, "M"), 其他=["--诊断"])
    文14 = p14.stdout + p14.stderr
    判("诊断·后缀＋密码窗口建得起来", "后缀＋密码窗口：✅" in 文14,
       [l for l in 文14.splitlines() if "后缀＋密码窗口" in l][:1])
    判("诊断·只密码窗口建得起来", "只密码窗口  ：✅" in 文14,
       [l for l in 文14.splitlines() if "只密码窗口" in l][:1])

    # 16 加密 ＋ 嵌套（照常见那类包的样子：伪装后缀 .mp3 ＋ 要密码 ＋ 嵌套两层）
    print("-" * 62)
    密巢 = os.path.join(BASE, "nestenc-" + 戳)
    os.makedirs(密巢, exist_ok=True)
    缓2 = io.BytesIO()
    with zipfile.ZipFile(缓2, "w") as z:
        z.writestr("里面.txt", "第二层的内容。")
    try:
        import pyzipper
        with pyzipper.AESZipFile(os.path.join(密巢, "要解压两次，防止检测.mp3"), "w",
                                 compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(b"hunter2")
            z.writestr("内层.bin", 缓2.getvalue())        # 内层也是 zip，名字不像
        辛2 = os.path.join(输出, "N")
        p15 = 跑(密巢, BOOK, 5.0, 辛2, 层数=2)
        文15 = p15.stdout + p15.stderr
        判("加密＋嵌套·外层第 2 个密码对上了", "第 2 个密码对上了" in 文15)
        # 报的「结果」必须等于盘上实测 —— 不许把被收走的中间层也算进去
        import re as _re
        包目录 = os.path.join(辛2, 出目录名("要解压两次，防止检测.mp3.zip"))
        实 = 0
        for _r, _, _fs in os.walk(包目录):
            for _f in _fs:
                实 += os.path.getsize(os.path.join(_r, _f))
        _m = _re.search(r"结果 ([\d.]+) (B|KB|MB|GB)", 文15)
        _报 = 0.0
        if _m:
            _报 = float(_m.group(1)) * {"B": 1, "KB": 1024, "MB": 1048576,
                                       "GB": 1073741824}[_m.group(2)]
        判("尺寸·报的「结果」= 盘上实测（没把收走的中间层算进去）",
           _m is not None and abs(_报 - 实) <= max(64.0, 实 * 0.05),
           "报 %.0f / 实 %d" % (_报, 实))

        判("加密＋嵌套·第二层也解出来了（摊平）",
           os.path.isfile(os.path.join(辛2, 出目录名("要解压两次，防止检测.mp3.zip"), "里面.txt")),
           [l for l in 文15.splitlines() if "内层" in l][:2])
    except ImportError:
        print("  （没装 pyzipper，跳过加密＋嵌套）")

    # 17 自动续解：三层套娃，**不指定层数**也要一路解到底
    print("-" * 62)
    三层 = os.path.join(BASE, "nest3-" + 戳)
    os.makedirs(三层, exist_ok=True)
    b1 = io.BytesIO()
    with zipfile.ZipFile(b1, "w") as z:
        z.writestr("最里面.txt", "第三层的内容。")
    b2 = io.BytesIO()
    with zipfile.ZipFile(b2, "w") as z:
        z.writestr("第二层.bin", b1.getvalue())
    with zipfile.ZipFile(os.path.join(三层, "三层套娃.mp3"), "w") as z:
        z.writestr("第一层.bin", b2.getvalue())
    壬2 = os.path.join(输出, "O")
    p16 = 跑(三层, None, 5.0, 壬2)              # 不传 --层数 ⇒ 走默认（自动）
    文16 = p16.stdout + p16.stderr
    判("自动续解·一路解到第三层（全摊平）",
       os.path.isfile(os.path.join(壬2, 出目录名("三层套娃.mp3.zip"), "最里面.txt")),
       文16.splitlines()[-8:])

    # 18 内层压缩包躺在**子文件夹**里（常见布局：外层/里面的mp3文件/要下载的文件.mp3）
    print("-" * 62)
    套层 = os.path.join(BASE, "nestdir-" + 戳)
    os.makedirs(套层, exist_ok=True)
    c1 = io.BytesIO()
    with zipfile.ZipFile(c1, "w") as z:
        z.writestr("最里面.txt", "第三层（最里面）的内容。")
    c2 = io.BytesIO()
    with zipfile.ZipFile(c2, "w") as z:
        z.writestr("里面的文件/内层.bin", c1.getvalue())          # 关键：塞进一个子文件夹
    with zipfile.ZipFile(os.path.join(套层, "伪装.mp3"), "w") as z:
        z.writestr("里面的mp3文件/要下载的文件.mp3", c2.getvalue())
    癸2 = os.path.join(输出, "P")
    p17 = 跑(套层, None, 5.0, 癸2)          # 默认（自动）
    文17 = p17.stdout + p17.stderr
    外 = os.path.join(癸2, 出目录名("伪装.mp3.zip"))
    判("内层在子文件夹里·也被找出来解了（摊平）",
       os.path.isfile(os.path.join(外, "最里面.txt")),
       文17.splitlines()[-9:])

    # 19 条目超时 —— 把上限压到 0.5 毫秒来验
    print("-" * 62)
    卯 = os.path.join(输出, "Q")
    p18 = 跑(BIG, None, 5.0, 卯, 其他=["--超时秒", "0.0005"])
    文18 = p18.stdout + p18.stderr
    判("超时·报里说「条目超时」并放弃", "条目超时" in 文18 and "已放弃" in 文18,
       文18.splitlines()[-8:])
    留 = [n for _, _, fs in os.walk(卯) for n in fs if n.endswith(".超时未完成")]
    判("超时·半成品改名成 .超时未完成（不留冒充成品的坏文件）", bool(留), 留)
    判("超时·汇总里点了超时数", "个条目超时被放弃" in 文18)

    # 20 0 字节都放弃（源文件与条目都不写出）
    print("-" * 62)
    辰 = os.path.join(BASE, "zero-" + 戳)
    os.makedirs(辰, exist_ok=True)
    open(os.path.join(辰, "空.mp3"), "wb").close()            # 0 字节的源文件
    with zipfile.ZipFile(os.path.join(辰, "有空的包.zip"), "w") as z:
        z.writestr("空的.txt", "")                           # 0 字节的条目
        z.writestr("有货.txt", "有内容")
    巳 = os.path.join(输出, "R")
    p19 = 跑(辰, None, 5.0, 巳)
    文19 = p19.stdout + p19.stderr
    判("0字节源文件·直接跳过", "源文件是 0 字节" in 文19)
    判("0字节条目·不写出空文件", not os.path.exists(os.path.join(巳, "有空的包", "空的.txt")))
    判("0字节条目·同包里有货的照旧解出",
       os.path.isfile(os.path.join(巳, "有空的包", "有货.txt")))
    判("0字节条目·报里点了数", "0 字节条目 1 个" in 文19)

    # 21 两个落点故意不一样：屏幕给原文，日志一路打码
    #    屏幕 = 原文；日志文件 = 打码
    print("-" * 62)
    短 = os.path.join(输出, "S")
    p20 = 跑(BIG, None, 5.0, 短)
    文20 = p20.stdout + p20.stderr
    找 = glob.glob(os.path.join(短, "_记录", "运行日志*.txt"))   # 收在 _记录\ 里，名字带日期+分钟
    L志 = max(找, key=os.path.getmtime) if 找 else os.path.join(短, "_记录", "运行日志.txt")
    志 = io.open(L志, encoding="utf-8", errors="replace").read() if os.path.isfile(L志) else ""
    判("屏幕·给使用者看的是真路径（原文）", BASE in 文20,
       [l for l in 文20.splitlines() if "文件夹" in l][:1])
    判("日志文件·存在", bool(志), L志)
    判("日志文件·**不出现真实路径**（已打码）", 志 and BASE not in 志)
    判("日志文件·确实换成了哈希", "#" in 志)
    判("日志文件·该留的还在：进度号 [1/", "[1/" in 志)
    判("日志文件·该留的还在：MBps 速度", "MBps" in 志)

    # 🔴 阴对照（2026-10-04 实测抓到的打码缺陷）：文件夹名里带**全角括号**时，
    #    老正则首段字符类排除了 `（）` ⇒ 整条匹配不上 ⇒ **一个字节都不码、原样进日志**。
    #    而网盘分享的文件夹名**大量带全角括号**（「（高清）」「（合集）」这类后缀）⇒ 打码形同虚设。
    #    ⚠️ 举例一律用合成的中性名 —— 别拿真实用户的文件夹名当例子写进代码（吃过这个亏）。
    夹括 = os.path.join(BASE, "带（括号）的夹-" + 戳)
    os.makedirs(夹括, exist_ok=True)
    shutil.copy2(os.path.join(FIX, "普通包.zip"), 夹括)
    卯括 = os.path.join(输出, "PAREN")
    跑(夹括, None, 5.0, 卯括, 其他=["--不递归"])
    找括 = glob.glob(os.path.join(卯括, "_记录", "运行日志*.txt"))
    志括 = io.open(max(找括, key=os.path.getmtime), encoding="utf-8",
                   errors="replace").read() if 找括 else ""
    判("打码·名字带**全角括号**的文件夹也必须被码掉（老正则在这条上整条失效）",
       "带（括号）的夹" not in 志括 and "（括号）" not in 志括,
       [l for l in 志括.splitlines() if "带" in l and "夹" in l][:2])
    判("打码·该留的还在（进度号没被顺带码掉）", "[1/" in 志括)

    # 22 「没解」必须自答（问题该在日志里，不该回头问使用者）
    print("-" * 62)
    假名 = os.path.join(BASE, "假格式-" + 戳)
    os.makedirs(假名, exist_ok=True)
    with open(os.path.join(假名, "冒充.mp3"), "wb") as f:
        f.write(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 200)
    亥 = os.path.join(输出, "T")
    p21 = 跑(假名, None, 5.0, 亥)
    文21 = p21.stdout + p21.stderr
    # ⚠️ **断言已翻面**（2026-10-04）：7z/RAR 现在**归本工具管**了 ⇒ 「认得出但开不了」不再成立。
    #    改判成：假 7z 头必须**说清是残包/假头**，且**不许**把「换个密码试试」当原因糊弄过去
    #    （7z 原文 `Cannot open the file as [7z] archive`，跟「加密头」是**两回事**、退出码却都是 2）
    判("假 7z 头·明说「残包或假头」，不甩锅给密码", "残包或假头" in 文21,
       [l for l in 文21.splitlines() if "预检" in l][:1])

    巢2 = os.path.join(BASE, "内层假格式-" + 戳)
    os.makedirs(巢2, exist_ok=True)
    缓假 = io.BytesIO()
    # ⚠️ 换成 **gzip** 库存：7z/RAR 现在归本工具管，不再属于「认不出」那一档；
    #    gzip/xz/tar 单文件仍不收（解出来是文件不是目录，形态不兼容），拿它才验得到那条路
    with zipfile.ZipFile(缓假, "w") as z:
        z.writestr("内层.gz", b"\x1f\x8b" + b"\x00" * 200)
    with zipfile.ZipFile(os.path.join(巢2, "外壳.mp3"), "w") as z:
        z.writestr("里面/内层.gz", 缓假.getvalue())
    子2 = os.path.join(输出, "U")
    p22 = 跑(巢2, None, 5.0, 子2)
    文22 = p22.stdout + p22.stderr
    判("内层·「是压缩包但不是 zip」的会被逐个列出来（拿 gzip 验；7z/RAR 已接管、不在此列）",
       "是压缩包但不是 zip" in 文22 and "gzip" in 文22,
       [l for l in 文22.splitlines() if "本层看过" in l or "不是 zip" in l][:2])
    判("内层·本层「看了几个／解了几个」也写清楚", "本层看过" in 文22)

    # 23 半成品不许当「已解过」跳过（否则那个包永远解不完）
    print("-" * 62)
    卯2 = os.path.join(输出, "V")
    跑(BIG, None, 5.0, 卯2, 其他=["--超时秒", "0.0005"])          # 必超时 ⇒ 半成品
    包名字 = os.path.join(卯2, 出目录名("大文件.zip"))
    判("半成品·留了 _未完成.txt", os.path.isfile(os.path.join(包名字, "_未完成.txt")))
    台账2 = os.path.join(卯2, "_记录", "已完成.txt")
    判("半成品·**没**写进台账（所以下次才会重做）",
       (not os.path.isfile(台账2)) or
       (os.path.basename(包名字) not in io.open(台账2, encoding="utf-8").read()), 台账2)
    p24 = 跑(BIG, None, 5.0, 卯2)                                  # 正常再跑（超时 60 秒，够）
    文24 = p24.stdout + p24.stderr
    判("半成品·再跑**不跳过**、而是挪走重做", "台账里却没记" in 文24 and "挪走重做" in 文24,
       文24.splitlines()[-14:])
    判("半成品·重做后这次真解成了", os.path.isfile(os.path.join(包名字, "大块.bin")))
    判("半成品·挪走的旧目录被收走（_记录\\_tmp 清空）",
       not os.path.isdir(os.path.join(卯2, "_记录", "_tmp")) or
       not os.listdir(os.path.join(卯2, "_记录", "_tmp")))

    # 24 卡死判定＝「有没有进展」，不是「总耗时」（单条目正常可能就要几分钟，拿总耗时判会误杀）
    print("-" * 62)
    亥2 = os.path.join(输出, "W")
    p25 = 跑(BIG, None, 5.0, 亥2, 其他=["--停滞秒", "0.000001"])
    文25 = p25.stdout + p25.stderr
    判("卡死·按「卡住多久没动静」判", "卡住" in 文25 and "条目超时" in 文25,
       文25.splitlines()[-8:])
    判("卡死·总耗时上限默认为「不限」（慢文件不被误杀）", "总耗时上限 不限" in 文25,
       [l for l in 文25.splitlines() if "时间闸" in l][:1])

    # 25 同名包**不能互相删**（否则后面的会把前面的结果当半成品挪走删掉）
    print("-" * 62)
    同名 = os.path.join(BASE, "同名-" + 戳)
    for 折 in ("a", "b", "c"):
        d = os.path.join(同名, 折)
        os.makedirs(d, exist_ok=True)
        with zipfile.ZipFile(os.path.join(d, "一样的.mp3"), "w") as z:
            z.writestr("内容.txt", "来自 " + 折)          # **同名条目**，好验集中模式的重名加 -1
    申 = os.path.join(输出, "X")
    跑(同名, None, 5.0, 申)
    有 = sorted(n for n in os.listdir(申) if n.startswith("一样的"))
    判("同名包·三个都留下来了（没互相删）", len(有) == 3, 有)
    判("同名包·三个目录里各自都有内容文件",
       all(len([f for f in os.listdir(os.path.join(申, n)) if f.startswith("内容")]) == 1
           for n in 有),
       [(n, os.listdir(os.path.join(申, n))) for n in 有])

    # 27 集中模式（出一档「全部合并到一个文件夹」，重名加 -1，且可切换）
    print("-" * 62)
    集 = os.path.join(输出, "Z")
    p27 = 跑(同名, None, 5.0, 集, 其他=["--集中"])
    文27 = p27.stdout + p27.stderr
    判("集中模式·报里标明「集中模式」", "集中模式" in 文27)
    集了 = sorted(n for n in os.listdir(集) if n.startswith("内容"))
    判("集中模式·全平铺到一个文件夹、重名自动加 -1（三个都在）",
       len(集了) == 3 and set(集了) == {"内容.txt", "内容-1.txt", "内容-2.txt"}, 集了)
    判("集中模式·没有留下包目录",
       not any(os.path.isdir(os.path.join(集, n)) and n.startswith("一样的")
               for n in os.listdir(集)))

    # 26 台账记着、目录却不在 ⇒ 必须重做（同名包互删之后的善后）
    print("-" * 62)
    酉 = os.path.join(输出, "Y")
    跑(BIG, None, 5.0, 酉)
    台账3 = os.path.join(酉, "_记录", "已完成.txt")
    判("自愈·正常跑完会进台账", os.path.isfile(台账3) and
       "大文件" in io.open(台账3, encoding="utf-8").read(), 台账3)
    包三 = os.path.join(酉, 出目录名("大文件.zip"))
    shutil.move(包三, os.path.join(酉, "_挪走"))          # 模拟「目录被删/被挪走了」
    p26 = 跑(BIG, None, 5.0, 酉)
    文26 = p26.stdout + p26.stderr
    判("自愈·台账记着但目录不在 ⇒ 不当已解过、重做", "当作没解过，重做" in 文26,
       文26.splitlines()[-12:])
    判("自愈·重做后目录回来了", os.path.isfile(os.path.join(包三, "大块.bin")))

    print("=" * 62)
    # 27 7z / RAR（2026-10-04 加：本工具现在也解 7z 与 RAR）
    print("-" * 62)
    七7 = os.path.join(输出, "7Z")
    p27 = 跑(FIX, None, 5.0, 七7, 一次密码=["错密码", "hunter2", "再来一条"])
    文27 = p27.stdout + p27.stderr
    判("7z·无加密的 7z（后缀乱写成 .mp3）靠**内容**认出来并解出",
       os.path.isfile(os.path.join(七7, 出目录名("真7z包.mp3.zip"), "中文名.txt")),
       [l for l in 文27.splitlines() if "7z/RAR" in l][:2])
    判("7z·包内子目录也**摊平**（与 zip 同规矩，只取文件名）",
       os.path.isfile(os.path.join(七7, 出目录名("真7z包.mp3.zip"), "里面.txt")))
    判("7z·加密 7z 的密码对上了", "7z/RAR" in 文27 and "第 2 个密码对上了" in 文27)
    判("7z·加密 7z 的内容真解出来了",
       os.path.isfile(os.path.join(七7, 出目录名("加密7z包.dat.zip"), "中文名.txt")))
    判("7z·**连文件名都加密**（-mhe）的包也解得开（得靠「换密码再列一次」）",
       os.path.isfile(os.path.join(七7, 出目录名("加密头7z包.dat.zip"), "中文名.txt")),
       [l for l in 文27.splitlines() if "加密头7z包" in l][:2])
    # 🔴 阴对照：7z 在「条目名一个都没匹配上」时**返回 0**，只信退出码 ⇒ 第一个候选必然假命中
    判("7z·**错密码绝不许当命中**（钉住「7z 匹配不上也返回 0」那个坑）",
       "第 1 个密码对上了" not in 文27,
       [l for l in 文27.splitlines() if "对上了" in l][:3])
    if os.path.isfile(os.path.join(FIX, "真RAR包.dat")):
        判("RAR·真 RAR 也解得出内容（含中文名、也按规矩摊平）",
           os.path.isfile(os.path.join(七7, 出目录名("真RAR包.dat.zip"), "你好.txt"))
           and os.path.isfile(os.path.join(七7, 出目录名("真RAR包.dat.zip"), "里面.txt")),
           [l for l in 文27.splitlines() if "真RAR" in l][:2])
    else:
        print("  （testdata\\真RAR样本.rar 不在 ⇒ RAR 这条没测；见 testdata\\README.md 怎么补）")
    if os.path.isfile(os.path.join(FIX, "真RAR4包.dat")):
        判("RAR4·**另一代** RAR（魔数第 7 字节是 00 那支）也解得出内容",
           bool(os.listdir(os.path.join(七7, 出目录名("真RAR4包.dat.zip")))),
           [l for l in 文27.splitlines() if "真RAR4" in l][:2])
    else:
        print("  （testdata\\真RAR4样本.rar 不在 ⇒ RAR4 这条没测）")

    # 30 文档（docx/xlsx/odt/epub…）**本身就是 zip**，不许被「自动续解」拆成 XML 碎片
    print("-" * 62)
    if os.path.isfile(os.path.join(FIX, "看着像表格.xlsx")):
        p31 = 跑(FIX, None, 5.0, 七7, 仅后缀="xlsx")
        文31 = p31.stdout + p31.stderr
        判("文档·顶层见到 xlsx **故意不拆**，并出声说明",
           "故意不拆" in 文31 and "交付物" in 文31,
           [l for l in 文31.splitlines() if "跳过" in l or "文档" in l][:2])
        判("文档·没有留下被拆开的 XML 碎片（xl/ 那一层不该出现）",
           not os.path.isdir(os.path.join(七7, 出目录名("看着像表格.xlsx.zip"), "xl")))
    包内表 = os.path.join(七7, 出目录名("里面有表格的包.zip"))
    if os.path.isdir(包内表):
        判("文档·**内层**的 xlsx 也不拆（只摊平它自己，不摊平它的内部）",
           os.path.isfile(os.path.join(包内表, "内层表格.xlsx"))
           and not os.path.isdir(os.path.join(包内表, "xl")),
           sorted(os.listdir(包内表))[:6])
        判("文档·同一包里**普通文件照解**（不是把整个包都跳过）",
           os.path.isfile(os.path.join(包内表, "普通.txt")))

    # 31 加密 7z 的**唯一条目是 0 字节** ⇒ 挑不出探针，但**不许误判成「都不对」**
    print("-" * 62)
    夹空 = os.path.join(BASE, "空条目7z-" + 戳)
    os.makedirs(夹空, exist_ok=True)
    if os.path.isfile(os.path.join(FIX, "加密空条目7z.dat")):
        shutil.copy2(os.path.join(FIX, "加密空条目7z.dat"), 夹空)
        p32 = 跑(夹空, None, 5.0, os.path.join(输出, "EMPTY7Z"),
                 一次密码=["错密码", "hunter2", "再来一条"])
        文32 = p32.stdout + p32.stderr
        判("空条目·不误判成「都不对」（假阴不许有）",
           "都不对" not in 文32,
           [l for l in 文32.splitlines() if "预检" in l or "不对" in l][:3])
    else:
        print("  （没造出 加密空条目7z.dat ⇒ 跳过）")

    # 32 「其实是 zip 的交付物」两条判据都要真的挡得住
    print("-" * 62)
    夹交 = os.path.join(BASE, "交付物-" + 戳)
    os.makedirs(夹交, exist_ok=True)
    if all(os.path.isfile(os.path.join(FIX, n)) for n in ("程序.jar", "作品.sb3", "改名包.mp3")):
        for n in ("程序.jar", "作品.sb3", "改名包.mp3", "名字像表格.xlsx", "带个通用名的包.zip"):
            if os.path.isfile(os.path.join(FIX, n)):
                shutil.copy2(os.path.join(FIX, n), 夹交)
        子交 = os.path.join(输出, "SHIP")
        p33 = 跑(夹交, None, 5.0, 子交)
        文33 = p33.stdout + p33.stderr
        # ⚠️ 断言改过：后缀名单**默认改空**之后，`.jar` 是靠**内容判据**挡住的。
        #    **行为没变**（照样挡），变的是「谁说中的」。
        判("交付物·`.jar` 被挡住，且**写明判据是「内容」**（后缀名单默认已空，不靠它了）",
           "包里带它的标志文件" in 文33, [l for l in 文33.splitlines() if "判据" in l][:2])
        判("交付物·**改名成 .mp3 的 jar** 也被挡住 —— 判据是**内容**不是后缀",
           "包里带它的标志文件" in 文33)
        # ⚠️ 工具是**摊平**的 ⇒ 要判**摊平后的文件**在不在，别拿目录判（那样永远为真、等于没测）
        判("交付物·被挡住的**一个零件文件都没写出来**",
           not os.path.isfile(os.path.join(子交, 出目录名("改名包.mp3.zip"), "MANIFEST.MF")))
        子全 = os.path.join(输出, "SHIPALL")
        p34 = 跑(夹交, None, 5.0, 子全, 其他=["--全部拆"])
        文34 = p34.stdout + p34.stderr
        # 🔴 这条是**真事**：名字像交付物、内容其实是普通分享包 ⇒ **必须照拆**
        判("交付物·**名字起得像 xlsx，但内容是普通分享包 ⇒ 照拆**（14 个漏解就是这形态）",
           os.path.isfile(os.path.join(子交, 出目录名("名字像表格.xlsx.zip"), "分享说明.txt")),
           [l for l in 文33.splitlines() if "名字像表格" in l][:2])
        # 🔴 **只有一个通用名文件、没有第二条证据 ⇒ 照拆**（不许一个同名文件就定性）
        判("交付物·**包里只有个 META-INF/MANIFEST.MF（没字节码）⇒ 照拆**（一个通用名不算数）",
           os.path.isfile(os.path.join(子交, 出目录名("带个通用名的包.zip"), "真正的分享内容.txt")),
           [l for l in 文33.splitlines() if "带个通用名" in l][:2])
        判("交付物·`--全部拆` 能推翻（这时才真拆）",
           "故意不拆" not in 文34
           and os.path.isfile(os.path.join(子全, 出目录名("改名包.mp3.zip"), "MANIFEST.MF")),
           [l for l in 文34.splitlines() if "跳过" in l][:2])
    else:
        print("  （交付物素材没造出来 ⇒ 跳过）")

    # 33 5 层套娃：**额度不按层数翻倍扣**（中间层一解开就当场收走、字节还回）
    print("-" * 62)
    夹套 = os.path.join(BASE, "五层-" + 戳)
    os.makedirs(夹套, exist_ok=True)
    if os.path.isfile(os.path.join(FIX, "五层套.zip")):
        shutil.copy2(os.path.join(FIX, "五层套.zip"), 夹套)
        p35 = 跑(夹套, None, 5.0, os.path.join(输出, "NEST5"))
        文35 = p35.stdout + p35.stderr
        成 = re.search(r"成功解出 ([\d.]+ \w+)", 文35)
        果 = re.search(r"结果产物 ([\d.]+ \w+)", 文35)
        判("五层套娃·**额度＝实际拿到的量**（成功解出 == 结果产物；改前是它的 3~5 倍）",
           成 and 果 and 成.group(1) == 果.group(1),
           "成功解出=%s 结果产物=%s" % (成 and 成.group(1), 果 and 果.group(1)))
        判("五层套娃·内层包是**当场收走**的（日志里有「当场收走」）", "当场收走" in 文35)
        判("五层套娃·但「过程产物」照样报出经手总量（别报成 0、看着像没中间层）",
           "过程产物" in 文35 and "过程产物 0 B" not in 文35,
           [l for l in 文35.splitlines() if "过程产物" in l][:1])
    else:
        print("  （没造出 五层套.zip ⇒ 跳过）")

    # 34 图种：头是 PNG、身子里是 **7z** ⇒ 只读头 8 字节的实现会整条漏掉
    print("-" * 62)
    夹种 = os.path.join(BASE, "图种-" + 戳)
    os.makedirs(夹种, exist_ok=True)
    if os.path.isfile(os.path.join(FIX, "图种7z.png")):
        shutil.copy2(os.path.join(FIX, "图种7z.png"), 夹种)
        p36 = 跑(夹种, None, 5.0, os.path.join(输出, "POLY"))
        文36 = p36.stdout + p36.stderr
        判("图种·**PNG 头 + 7z 身子**也认得出来（只看头 8 字节就会漏）",
           "7z/RAR" in 文36 and "真身是 PNG" not in 文36,
           [l for l in 文36.splitlines() if "预检" in l][:2])
        判("图种·里面那个文件真解出来了",
           os.path.isfile(os.path.join(输出, "POLY", 出目录名("图种7z.png.zip"), "图种里的.txt")))
    else:
        print("  （图种素材没造出来 ⇒ 跳过）")

    print("=" * 62)
    print("结果：%d 过 / %d 败" % (右, 错))
    print("产物保留在：%s" % BASE)
    print("=" * 62)
    return 1 if 错 else 0


if __name__ == "__main__":
    sys.exit(主())
